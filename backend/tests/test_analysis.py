"""End-to-end pipeline tests with fake Gemini, Fact Check and source provider."""

import json

import httpx

from app import analysis, source_provider
from app.analysis import (
    NO_CLAIM_EXPLANATION,
    NO_SOURCES_EXPLANATION,
    _rating_to_status,
    run_investigation,
)
from app.config import get_settings
from app.models import AnalysisInput, GraphEdge, GraphNode, Source, SourceBundle
from tests.conftest import gemini_body, make_http_client

CLAIM = "The city cut its library budget by 40% this year."
FACTCHECK_BODY = {
    "claims": [
        {
            "text": "The city cut its library budget by 40% this year",
            "claimReview": [
                {
                    "publisher": {"name": "Fact Desk"},
                    "url": "https://factdesk.example/library",
                    "title": "Library cut claim",
                    "reviewDate": "2026-10-02",
                    "textualRating": "False",
                }
            ],
        }
    ]
}


# Builds a fake upstream handler with configurable Gemini and fact-check replies.
def make_handler(extraction=None, verdict=None, factcheck=None, fail=()):
    """Return a MockTransport handler routing by host and prompt type."""

    # Routes a request to the right canned response.
    def handler(request: httpx.Request) -> httpx.Response:
        """Return the canned response for this upstream call."""
        if request.url.host == "factchecktools.googleapis.com":
            if "factcheck" in fail:
                return httpx.Response(500)
            return httpx.Response(200, json=factcheck or {})
        prompt_text = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        is_extraction = prompt_text.startswith("<page_text>")
        if ("extract" if is_extraction else "analyze") in fail:
            return httpx.Response(503)
        if is_extraction:
            return httpx.Response(
                200, json=gemini_body(extraction or {"checkable": True, "claim": CLAIM})
            )
        return httpx.Response(200, json=gemini_body(verdict))

    return handler


# Runs the pipeline once with the given handler and input.
async def investigate(handler, text=CLAIM, mode="manual"):
    """Return the AnalyzeResponse for one fake investigation."""
    analysis_input = AnalysisInput(text=text, url="https://news.example/a", mode=mode)
    async with make_http_client(handler) as client:
        return await run_investigation(client, get_settings(), analysis_input)


# Checks the happy path with a fact-check produces a cited verdict.
async def test_contradicted_with_fact_check():
    """A fact-check plus a cited verdict yields contradicted with sources."""
    handler = make_handler(
        verdict={
            "status": "contradicted",
            "explanation": "Fact Desk rated this false [F1].",
            "cited_source_ids": ["F1"],
        },
        factcheck=FACTCHECK_BODY,
    )
    result = await investigate(handler)
    assert result.status == "contradicted"
    assert result.claim == CLAIM
    assert [source.id for source in result.sources] == ["F1"]
    assert result.graph_nodes[0].kind == "fact_check"
    assert len(result.investigation_id) == 32


# Checks no sources at all gives insufficient_evidence without calling analysis.
async def test_no_sources_is_insufficient():
    """With zero sources the result is neutral and analysis is never called."""
    handler = make_handler(fail=("analyze",))
    result = await investigate(handler)
    assert result.status == "insufficient_evidence"
    assert result.explanation == NO_SOURCES_EXPLANATION
    assert result.sources == [] and result.graph_edges == []


# Checks a non-checkable text returns a neutral result without retrieval.
async def test_no_checkable_claim():
    """Opinion text returns insufficient_evidence and a preview as claim."""
    handler = make_handler(extraction={"checkable": False, "claim": ""})
    result = await investigate(handler, text="I think pizza is great.")
    assert result.status == "insufficient_evidence"
    assert result.explanation == NO_CLAIM_EXPLANATION
    assert result.claim == "I think pizza is great."


# Checks an invented citation from Gemini cannot produce a verdict.
async def test_invented_citation_is_downgraded():
    """If Gemini cites only a non-existent source, the verdict is neutral."""
    handler = make_handler(
        verdict={
            "status": "supported",
            "explanation": "Confirmed by Reuters [S9].",
            "cited_source_ids": ["S9"],
        },
        factcheck=FACTCHECK_BODY,
    )
    result = await investigate(handler)
    assert result.status == "insufficient_evidence"
    assert "S9" not in result.explanation


# Checks Gemini extraction failure falls back to the selected text.
async def test_extraction_failure_uses_selection():
    """A short manual selection is used directly when extraction fails."""
    handler = make_handler(
        fail=("extract",),
        verdict={
            "status": "contradicted",
            "explanation": "Rated false [F1].",
            "cited_source_ids": ["F1"],
        },
        factcheck=FACTCHECK_BODY,
    )
    result = await investigate(handler)
    assert result.claim == CLAIM
    assert result.status == "contradicted"


# Checks long automatic text falls back to its first usable sentence.
async def test_extraction_failure_long_text_uses_first_sentence():
    """For long page text, the first 20-400 char sentence is the claim."""
    page_text = "Hi. " + CLAIM + " " + ("More words here. " * 40)
    result = await investigate(
        make_handler(fail=("extract",)), text=page_text, mode="automatic"
    )
    assert result.claim == CLAIM


# Checks analysis failure falls back to a matching fact-check rating.
async def test_analysis_failure_falls_back_to_fact_check():
    """With Gemini analysis down, a matching 'False' rating gives contradicted."""
    handler = make_handler(fail=("analyze",), factcheck=FACTCHECK_BODY)
    result = await investigate(handler)
    assert result.status == "contradicted"
    assert "[F1]" in result.explanation
    assert "unavailable" in result.explanation


# Checks the fallback refuses to use fact-checks about a different claim.
async def test_fallback_ignores_unrelated_fact_check():
    """A fact-check about another claim never produces a verdict."""
    unrelated_body = json.loads(json.dumps(FACTCHECK_BODY))
    unrelated_body["claims"][0]["text"] = "Vaccines contain microchips"
    handler = make_handler(fail=("analyze",), factcheck=unrelated_body)
    result = await investigate(handler)
    assert result.status == "insufficient_evidence"
    assert len(result.sources) == 1


# Checks every service failing still returns a valid neutral result.
async def test_everything_down_still_returns():
    """Extraction, fact-check and analysis all failing gives a neutral reply."""
    result = await investigate(make_handler(fail=("extract", "analyze", "factcheck")))
    assert result.status == "insufficient_evidence"
    assert result.claim == CLAIM


# Checks provider sources, edges and earliest source are merged and cleaned.
async def test_provider_bundle_is_merged_and_cleaned(monkeypatch):
    """Provider data is merged with fact-checks and bad edges are dropped."""
    earliest = Source(
        id="S1", title="Orig", url="https://a.example/", date="2026-01-01"
    )

    # Fake Person 3 provider returning sources, nodes and edges.
    async def fake_fetch_sources(claim):
        """Return a bundle with one good edge and two bad ones."""
        return SourceBundle(
            sources=[
                earliest,
                Source(id="S2", title="Copy", url="https://b.example/"),
                Source(id="F1", title="Collides", url="https://c.example/"),
            ],
            graph_nodes=[
                GraphNode(id="S1", title="Orig", url="https://a.example/"),
                GraphNode(id="S2", title="Copy", url="https://b.example/"),
            ],
            graph_edges=[
                GraphEdge(
                    source_id="S2",
                    target_id="S1",
                    relationship_type="explicit_citation",
                    evidence="S2 links to S1",
                ),
                GraphEdge(
                    source_id="S2",
                    target_id="S1",
                    relationship_type="similar_wording",
                    evidence="",
                ),
                GraphEdge(
                    source_id="S2",
                    target_id="S99",
                    relationship_type="similar_wording",
                    evidence="x",
                ),
            ],
            earliest_observed_source=earliest,
        )

    monkeypatch.setattr(source_provider, "fetch_sources", fake_fetch_sources)
    handler = make_handler(
        verdict={
            "status": "contradicted",
            "explanation": "Rated false [F1]; original says 4% [S1].",
            "cited_source_ids": ["F1", "S1"],
        },
        factcheck=FACTCHECK_BODY,
    )
    result = await investigate(handler)
    assert [source.id for source in result.sources] == ["F1", "S1", "S2"]
    assert result.sources[0].url == "https://factdesk.example/library"
    assert [node.id for node in result.graph_nodes] == ["F1", "S1", "S2"]
    assert len(result.graph_edges) == 1
    assert result.earliest_observed_source.id == "S1"
    assert result.status == "contradicted"


# Checks a crashing provider and failing persistence do not break the reply.
async def test_provider_and_persistence_failures_are_contained(monkeypatch):
    """Exceptions from Person 3 hooks are logged, not raised."""

    # Simulates a broken provider.
    async def broken_fetch(claim):
        """Raise to simulate a Bright Data outage."""
        raise RuntimeError("bright data down")

    # Simulates a broken database.
    async def broken_save(result):
        """Raise to simulate a Neon outage."""
        raise RuntimeError("db down")

    monkeypatch.setattr(source_provider, "fetch_sources", broken_fetch)
    monkeypatch.setattr(source_provider, "save_investigation", broken_save)
    handler = make_handler(factcheck=FACTCHECK_BODY, fail=("analyze",))
    result = await investigate(handler)
    assert result.status == "contradicted"


# Checks the persistence hook receives the final result.
async def test_result_is_passed_to_persistence(monkeypatch):
    """save_investigation is called once with the returned result."""
    saved_results = []

    # Captures saved investigations.
    async def capture_save(result):
        """Append the result to saved_results."""
        saved_results.append(result)

    monkeypatch.setattr(source_provider, "save_investigation", capture_save)
    result = await investigate(make_handler())
    await analysis.drain_background_tasks()  # saving runs after the reply
    assert saved_results == [result]


# Checks earliest source is dropped if it is not among the final sources.
async def test_earliest_source_must_be_a_returned_source(monkeypatch):
    """An earliest source that was filtered out is not reported."""

    # Returns an earliest source with an unsafe URL.
    async def fake_fetch(claim):
        """Return a bundle whose earliest source was not kept."""
        bad_source = Source(
            id="S1", title="bad", url="javascript:x", date="2020-01-01"
        )
        return SourceBundle(sources=[bad_source], earliest_observed_source=bad_source)

    monkeypatch.setattr(source_provider, "fetch_sources", fake_fetch)
    result = await investigate(make_handler())
    assert result.earliest_observed_source is None
    assert result.status == "insufficient_evidence"


# Checks the fact-check rating mapping on common real-world ratings.
def test_rating_mapping():
    """Clear ratings map to statuses; vague ones map to None."""
    assert _rating_to_status("False") == "contradicted"
    assert _rating_to_status("Pants on Fire!") == "contradicted"
    assert _rating_to_status("Not true") == "contradicted"
    assert _rating_to_status("Mostly False") == "misleading"
    assert _rating_to_status("Half-True") == "misleading"
    assert _rating_to_status("Missing context") == "misleading"
    assert _rating_to_status("True") == "supported"
    assert _rating_to_status("Mostly True") is None
    assert _rating_to_status("Unproven") is None
    assert _rating_to_status("") is None
    assert analysis._word_overlap("", "abc") == 0.0

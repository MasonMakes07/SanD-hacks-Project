"""Tests for the source_tracing integration (no network, no database)."""

import json

from source_tracing.models import TraceResult
from source_tracing.service import MOCK_FIXTURE_PATH

from app import source_provider
from app.models import AnalyzeResponse, GraphEdge, GraphNode
from app.validation import clean_edges
from tests.conftest import REAL_FETCH_SOURCES, REAL_SAVE_INVESTIGATION

FIXTURE_RESULT = TraceResult.model_validate(
    json.loads(MOCK_FIXTURE_PATH.read_text(encoding="utf-8"))
)


# Fake Neon store that records save_analysis calls.
class RecordingStore:
    """Collects save_analysis arguments instead of writing to Postgres."""

    backend = "neon_postgres"

    def __init__(self):
        self.saved = []

    # Records one analysis.
    def save_analysis(self, *args):
        """Append the call arguments to self.saved."""
        self.saved.append(args)


# Checks Person 3's real contract payload converts into a SourceBundle.
async def test_fetch_sources_converts_trace_result(monkeypatch):
    """Sources, nodes, evidence-object edges and the trace id carry over."""

    # Returns Person 3's labelled fixture instead of tracing live.
    async def fake_trace_claim(claim):
        """Return the mock TraceResult."""
        return FIXTURE_RESULT

    monkeypatch.setattr(source_provider, "trace_claim", fake_trace_claim)
    bundle = await REAL_FETCH_SOURCES("any claim")
    payload = FIXTURE_RESULT.to_contract_dict()
    assert [source.id for source in bundle.sources] == [
        source["id"] for source in payload["sources"]
    ]
    assert len(bundle.graph_nodes) == len(payload["graph_nodes"])
    assert len(bundle.graph_edges) == len(payload["graph_edges"])
    assert bundle.investigation_id == payload["investigation_id"]
    assert all(isinstance(edge.evidence, dict) for edge in bundle.graph_edges)
    assert clean_edges(bundle.graph_edges, bundle.graph_nodes) == bundle.graph_edges


# Checks a failed (EMPTY-) trace does not lend its id to the response.
async def test_fetch_sources_drops_empty_trace_id(monkeypatch):
    """EMPTY- results were never saved, so their id is not reused."""

    # Returns an empty failure result like Person 3's service does.
    async def fake_trace_claim(claim):
        """Return an EMPTY- TraceResult."""
        return TraceResult(
            investigation_id="EMPTY-20261114T000000",
            sources=[],
            earliest_observed_source=None,
            graph_nodes=[],
            graph_edges=[],
        )

    monkeypatch.setattr(source_provider, "trace_claim", fake_trace_claim)
    bundle = await REAL_FETCH_SOURCES("any claim")
    assert bundle.investigation_id is None and bundle.sources == []


# Checks evidence objects without any text are dropped like blank strings.
def test_clean_edges_rejects_empty_evidence_object():
    """An evidence object with no excerpt, hyperlink or target text is dropped."""
    nodes = [
        GraphNode(id="s1", title="a", url="https://a.example/"),
        GraphNode(id="s2", title="b", url="https://b.example/"),
    ]
    edges = [
        GraphEdge(
            source_id="s1",
            target_id="s2",
            relationship_type="similar_wording",
            evidence={"excerpt": "  ", "hyperlink": None, "note": "overlap"},
        ),
        GraphEdge(
            source_id="s2",
            target_id="s1",
            relationship_type="explicit_citation",
            evidence={"excerpt": "", "hyperlink": "https://a.example/"},
        ),
    ]
    kept_edges = clean_edges(edges, nodes)
    assert [edge.relationship_type for edge in kept_edges] == ["explicit_citation"]


# Checks the verdict is written to Neon with the full response.
async def test_save_investigation_writes_analysis(monkeypatch):
    """save_analysis receives id, claim, status, explanation and the response."""
    store = RecordingStore()
    monkeypatch.setattr(source_provider, "_analysis_store", lambda: store)
    result = AnalyzeResponse(
        investigation_id="WEB-abc",
        claim="Claim.",
        status="insufficient_evidence",
        explanation="Not enough.",
        sources=[],
        earliest_observed_source=None,
        graph_nodes=[],
        graph_edges=[],
    )
    await REAL_SAVE_INVESTIGATION(result)
    analysis_id, investigation_id, claim, status, explanation, response = store.saved[0]
    assert len(analysis_id) == 32 and investigation_id == "WEB-abc"
    assert claim == "Claim." and status == "insufficient_evidence"
    assert explanation == "Not enough."
    assert response == result.model_dump(mode="json")


# Checks nothing is written when no Neon store is configured.
async def test_save_investigation_without_neon_is_noop(monkeypatch):
    """Stores without save_analysis (local JSON, none) are skipped."""

    # Store without save_analysis, like NullStore.
    class NoDatabaseStore:
        """Stand-in for NullStore."""

        backend = "none"

    monkeypatch.setattr(source_provider, "_analysis_store", lambda: NoDatabaseStore())
    await REAL_SAVE_INVESTIGATION(
        AnalyzeResponse(
            investigation_id="x",
            claim="c",
            status="insufficient_evidence",
            explanation="e",
            sources=[],
            earliest_observed_source=None,
            graph_nodes=[],
            graph_edges=[],
        )
    )


# Checks social_post nodes from source_tracing survive conversion.
async def test_fetch_sources_keeps_social_post_nodes(monkeypatch):
    """A social post node is converted with its kind, not dropped."""
    post_url = "https://x.com/u/status/9"

    # Returns a trace with one article and one social post.
    async def fake_trace_claim(claim):
        """Return a TraceResult containing a social_post node."""
        return TraceResult.model_validate(
            {
                "investigation_id": "WEB-social",
                "sources": [
                    {
                        "id": "s1",
                        "title": "a",
                        "url": "https://n.example/",
                        "date": None,
                        "excerpt": "",
                    },
                    {"id": "s2", "title": "post", "url": post_url, "date": None,
                     "excerpt": "x"},
                ],
                "earliest_observed_source": None,
                "graph_nodes": [
                    {"id": "s1", "title": "a", "url": "https://n.example/"},
                    {
                        "id": "s2",
                        "title": "post",
                        "url": post_url,
                        "kind": "social_post",
                    },
                ],
                "graph_edges": [],
            }
        )

    monkeypatch.setattr(source_provider, "trace_claim", fake_trace_claim)
    bundle = await REAL_FETCH_SOURCES("any claim")
    assert [node.kind for node in bundle.graph_nodes] == ["article", "social_post"]


# Checks a failed trace is logged instead of silently returning no sources.
async def test_trace_failure_is_logged(monkeypatch, caplog):
    """The trace error reaches the log; the empty result is still returned."""

    # Simulates Person 3's service reporting a Bright Data failure.
    async def failing_detailed(claim):
        """Return an empty result with an error message."""

        # Minimal stand-in for CachedTrace.
        class Detailed:
            """Holds result and error like CachedTrace."""

            error = "source search failed: SERP response was not JSON"
            result = TraceResult(
                investigation_id="EMPTY-1",
                sources=[],
                earliest_observed_source=None,
                graph_nodes=[],
                graph_edges=[],
            )

        return Detailed()

    monkeypatch.setattr(source_provider, "trace_claim_detailed", failing_detailed)
    with caplog.at_level("WARNING", logger="truthtrace.sources"):
        result = await source_provider.trace_claim("claim")
    assert result.sources == []
    assert "SERP response was not JSON" in caplog.text

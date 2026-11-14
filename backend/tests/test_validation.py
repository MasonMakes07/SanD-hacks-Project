"""Tests for citation validation and graph cleaning."""

from app.models import AnalysisVerdict, GraphEdge, GraphNode, Source
from app.validation import (
    NO_VERDICT_EXPLANATION,
    clean_edges,
    clean_nodes,
    clean_sources,
    validate_verdict,
)

SOURCES = [
    Source(id="F1", title="FC", url="https://fc.example/1", excerpt="rated False"),
    Source(id="S1", title="News", url="https://news.example/1", excerpt="4% cut"),
]


# Builds a verdict with the given fields for brevity in tests.
def verdict(status, explanation, cited=None) -> AnalysisVerdict:
    """Return an AnalysisVerdict with an optional cited id list."""
    return AnalysisVerdict(status, explanation, cited or [])


# Checks valid citations are kept and reported.
def test_valid_citations_are_kept():
    """A verdict citing real ids keeps its status and citations."""
    result = validate_verdict(
        verdict("contradicted", "Budget fell 4% [S1]. Rated false [F1].", ["S1"]),
        SOURCES,
    )
    assert result.status == "contradicted"
    assert result.explanation == "Budget fell 4% [S1]. Rated false [F1]."
    assert result.cited_source_ids == ["S1", "F1"]


# Checks invented citation ids are removed from the explanation.
def test_invented_citations_are_stripped():
    """Ids not in the retrieved sources are removed, real ones kept."""
    result = validate_verdict(
        verdict("contradicted", "Fell 4% [S1, S9]. Also [S7] says so.", ["S9"]),
        SOURCES,
    )
    assert result.status == "contradicted"
    assert "S9" not in result.explanation and "S7" not in result.explanation
    assert result.explanation == "Fell 4% [S1]. Also says so."
    assert result.cited_source_ids == ["S1"]


# Checks a verdict with only invented citations is downgraded.
def test_verdict_with_no_valid_citations_is_downgraded():
    """A non-neutral status without any valid citation becomes insufficient."""
    result = validate_verdict(
        verdict("contradicted", "This is false [S5].", ["S5"]), SOURCES
    )
    assert result.status == "insufficient_evidence"
    assert result.explanation == NO_VERDICT_EXPLANATION
    assert result.cited_source_ids == []


# Checks cited_source_ids alone (without markers) do not justify a verdict.
def test_ids_list_without_markers_does_not_count():
    """Citations must appear in the explanation text to count."""
    result = validate_verdict(verdict("supported", "It is true.", ["S1"]), SOURCES)
    assert result.status == "insufficient_evidence"


# Checks URLs written by the model are removed.
def test_urls_in_explanation_are_removed():
    """Model-written URLs never reach the user."""
    result = validate_verdict(
        verdict("supported", "See https://evil.example/x and www.bad.example [S1]."),
        SOURCES,
    )
    assert "http" not in result.explanation and "www." not in result.explanation
    assert result.status == "supported"


# Checks unknown statuses are downgraded.
def test_unknown_status_is_downgraded():
    """A status outside the allowed set becomes insufficient_evidence."""
    result = validate_verdict(verdict("false", "Wrong [S1]."), SOURCES)
    assert result.status == "insufficient_evidence"


# Checks insufficient_evidence without citations is allowed and keeps its text.
def test_insufficient_without_citations_keeps_text():
    """Neutral verdicts need no citation."""
    result = validate_verdict(
        verdict("insufficient_evidence", "Sources discuss a different claim."),
        SOURCES,
    )
    assert result.status == "insufficient_evidence"
    assert result.explanation == "Sources discuss a different claim."


# Checks an empty explanation is replaced with the default text.
def test_empty_explanation_gets_default():
    """An empty neutral explanation is replaced with the safe default."""
    result = validate_verdict(verdict("insufficient_evidence", "   "), SOURCES)
    assert result.explanation == NO_VERDICT_EXPLANATION


# Checks bracketed non-citation text such as years is left alone.
def test_non_citation_brackets_are_untouched():
    """Brackets like [2024] or [sic] are not treated as citations."""
    result = validate_verdict(
        verdict("supported", "In [2024] he said [sic] it rose [S1]."), SOURCES
    )
    assert result.explanation == "In [2024] he said [sic] it rose [S1]."


# Checks sources are deduplicated and non-web URLs dropped.
def test_clean_sources_dedupes_and_filters():
    """Duplicate ids/URLs and non-http URLs are removed, order preserved."""
    raw_sources = SOURCES + [
        Source(id="S1", title="dup id", url="https://other.example/"),
        Source(id="S2", title="dup url", url="https://news.example/1"),
        Source(id="S3", title="bad", url="javascript:alert(1)"),
        Source(id="", title="no id", url="https://x.example/"),
        Source(id="S4", title="ok", url="http://ok.example/"),
    ]
    assert [source.id for source in clean_sources(raw_sources)] == ["F1", "S1", "S4"]


# Checks edges without evidence or with missing endpoints are dropped.
def test_clean_edges_enforces_edge_rules():
    """Only evidence-backed, non-loop, de-duplicated edges between nodes stay."""
    nodes = clean_nodes(
        [
            GraphNode(id="A", title="a", url="https://a.example/"),
            GraphNode(id="B", title="b", url="https://b.example/"),
            GraphNode(id="A", title="dup", url="https://a2.example/"),
            GraphNode(id="C", title="bad", url="ftp://c.example/"),
        ]
    )
    assert [node.id for node in nodes] == ["A", "B"]
    edges = [
        GraphEdge(
            source_id="A",
            target_id="B",
            relationship_type="explicit_citation",
            evidence="  A links to B  ",
        ),
        GraphEdge(
            source_id="A",
            target_id="B",
            relationship_type="explicit_citation",
            evidence="duplicate",
        ),
        GraphEdge(
            source_id="A",
            target_id="B",
            relationship_type="similar_wording",
            evidence="   ",
        ),
        GraphEdge(
            source_id="A",
            target_id="A",
            relationship_type="similar_wording",
            evidence="self",
        ),
        GraphEdge(
            source_id="A",
            target_id="C",
            relationship_type="similar_wording",
            evidence="missing node",
        ),
    ]
    kept_edges = clean_edges(edges, nodes)
    assert len(kept_edges) == 1
    assert kept_edges[0].evidence == "A links to B"

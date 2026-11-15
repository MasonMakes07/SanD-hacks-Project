"""Fixed mock response so the extension can be built before real services work."""

from app.models import AnalyzeResponse, GraphEdge, GraphNode, Source

MOCK_SOURCES = [
    Source(
        id="F1",
        title="Fact check: Did the city cut its library budget by 40%?",
        url="https://example.org/fact-check/library-budget",
        date="2026-10-02",
        excerpt=(
            'Example Fact Desk fact-checked "The city cut its library budget by '
            '40% this year" and rated it: False.'
        ),
    ),
    Source(
        id="S1",
        title="City council approves 2026 budget",
        url="https://example.com/news/council-approves-budget",
        date="2026-09-15",
        excerpt="The approved budget reduces library funding by 4%, from $25M to $24M.",
    ),
    Source(
        id="S2",
        title="Viral post misstates library cuts",
        url="https://example.net/blog/library-cuts-viral",
        date=None,
        excerpt="A widely shared post claims a 40% cut, citing the council budget.",
    ),
]

MOCK_RESPONSE = AnalyzeResponse(
    investigation_id="mock-0000000000000000",
    cited_source_ids=["S1", "F1"],
    provenance={"source_trace": {"cache_hit": False, "storage_backend": "mock", "saved": False},
                "saved_analysis": {"saved": False, "storage_backend": "mock"}},
    claim="The city cut its library budget by 40% this year.",
    status="contradicted",
    explanation=(
        "The approved city budget reduces library funding by about 4%, not 40% "
        "[S1]. A published fact-check rated the 40% figure false [F1]."
    ),
    sources=MOCK_SOURCES,
    earliest_observed_source=MOCK_SOURCES[1],
    graph_nodes=[
        GraphNode(
            id="F1",
            title=MOCK_SOURCES[0].title,
            url=MOCK_SOURCES[0].url,
            date=MOCK_SOURCES[0].date,
            kind="fact_check",
        ),
        GraphNode(
            id="S1",
            title=MOCK_SOURCES[1].title,
            url=MOCK_SOURCES[1].url,
            date=MOCK_SOURCES[1].date,
        ),
        GraphNode(
            id="S2",
            title=MOCK_SOURCES[2].title,
            url=MOCK_SOURCES[2].url,
            date=None,
        ),
    ],
    graph_edges=[
        GraphEdge(
            source_id="S2",
            target_id="S1",
            relationship_type="explicit_citation",
            evidence="S2 links to the council budget article (S1).",
        ),
        GraphEdge(
            source_id="F1",
            target_id="S1",
            relationship_type="explicit_citation",
            evidence="The fact-check cites the approved budget figures from S1.",
        ),
    ],
)


# Returns a fresh copy of the fixed mock response.
def build_mock_response() -> AnalyzeResponse:
    """Return a deep copy of MOCK_RESPONSE so callers cannot mutate the original."""
    return MOCK_RESPONSE.model_copy(deep=True)

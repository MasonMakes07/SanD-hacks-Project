"""Integration point for Person 3 (Bright Data SERP, edge detection, Neon DB).

Person 2 does NOT implement these. The stubs below keep the pipeline runnable
until Person 3 replaces their bodies. Contract Person 3 must follow:

fetch_sources(claim) -> SourceBundle
    sources:     list[Source], ids "S1", "S2", ... (unique; never start with "F")
    graph_nodes: list[GraphNode], one per source id
    graph_edges: list[GraphEdge] between node ids, each with non-empty evidence
    earliest_observed_source: Source with a reliable date, else None

save_investigation(result) -> None
    Store the finished AnalyzeResponse (investigations, sources, edges tables).
"""

from app.models import AnalyzeResponse, SourceBundle


# STUB (Person 3): returns no sources until the Bright Data module is plugged in.
async def fetch_sources(claim: str) -> SourceBundle:
    """Return SERP sources, graph nodes, edges and earliest source for a claim."""
    return SourceBundle()


# STUB (Person 3): no-op until the Neon Postgres module is plugged in.
async def save_investigation(result: AnalyzeResponse) -> None:
    """Persist a finished investigation to the database."""
    return None

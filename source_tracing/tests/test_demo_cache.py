"""Offline gate for the retrieved public-web demo backup (no vendor calls)."""

import asyncio
import json
import socket

from source_tracing.demo_cache import CLAIM, EXAMPLE_RESPONSE, OUT_DIR, SOURCE_URLS
from source_tracing.models import TraceResult
from source_tracing.service import build_service


def test_real_demo_backup_is_evidence_backed_and_reusable(monkeypatch):
    files = list(OUT_DIR.glob("WEB-*.json"))
    assert files
    record = max((json.loads(path.read_text(encoding="utf-8")) for path in files),
                 key=lambda value: value["retrieved_at"])
    assert record["storage"] == "local_json_demo_backup_not_neon"
    assert record["meta"]["source_method"] == "public_web_manual_seed_not_brightdata"
    assert record["meta"]["serp_requests"] == 0
    result = TraceResult.model_validate(record["result"])
    assert json.loads(EXAMPLE_RESPONSE.read_text(encoding="utf-8")) == result.to_contract_dict()
    assert [source.url for source in result.sources] == list(SOURCE_URLS)
    assert result.earliest_observed_source in result.sources
    assert any(edge.relationship_type == "explicit_citation" and edge.source_id == "s1"
               and edge.target_id == "s2" and edge.evidence.hyperlink == SOURCE_URLS[1]
               for edge in result.graph_edges)
    assert all(edge.evidence.retrieved_at.isoformat() for edge in result.graph_edges)

    def blocked(*args, **kwargs):
        raise AssertionError("cache hit must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    service = build_service(env={"TRUTHTRACE_CACHE_DIR": str(OUT_DIR)})
    hit = asyncio.run(service.trace(CLAIM))
    assert hit.cache_hit and hit.storage_backend == "local_json"
    assert hit.result == result and hit.retrieved_at.isoformat() == record["retrieved_at"]

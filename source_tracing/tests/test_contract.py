"""Offline contract tests for Person 3's tracing result. No network, no DB."""

from __future__ import annotations

import asyncio
import copy
import json
import socket
from typing import get_args

import pytest
from pydantic import ValidationError

import source_tracing
from source_tracing import (
    GraphEdge,
    RelationshipType,
    Source,
    TraceResult,
    trace_claim,
)
from source_tracing.service import MOCK_FIXTURE_PATH, MOCK_ID_PREFIX

# Field names from Plan.md section 4 and PERSON3_CONTRACT.md.
RESULT_FIELDS = {
    "investigation_id",
    "sources",
    "earliest_observed_source",
    "graph_nodes",
    "graph_edges",
}
SOURCE_FIELDS = {"id", "title", "url", "date", "excerpt"}
NODE_FIELDS = {"id", "title", "url"}
EDGE_FIELDS = {"source_id", "target_id", "relationship_type", "evidence"}
EVIDENCE_FIELDS = {
    "url", "excerpt", "hyperlink", "anchor_text", "target_url", "target_excerpt",
    "retrieved_at", "similarity", "method", "note",
}
RELATIONSHIPS = {"explicit_citation", "similar_wording", "same_fact_check"}
# Person 2 owns these; Person 3 must never emit them.
PERSON2_FIELDS = {"claim", "status", "explanation"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setenv("TRUTHTRACE_MOCK", "1")  # these tests exercise the labelled mock path
    def blocked(*args, **kwargs):
        raise AssertionError("Stage 1 must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def fixture_data() -> dict:
    return json.loads(MOCK_FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def result() -> TraceResult:
    return run(trace_claim("An illustrative claim", "https://example.org/page"))


def test_public_import_and_signature():
    import inspect

    assert asyncio.iscoroutinefunction(source_tracing.trace_claim)
    params = inspect.signature(trace_claim).parameters
    assert list(params) == ["claim", "seed_url"]
    assert params["seed_url"].default is None


def test_seed_url_is_optional():
    assert isinstance(run(trace_claim("An illustrative claim")), TraceResult)


def test_exact_top_level_schema(result):
    payload = result.to_contract_dict()
    assert set(payload) == RESULT_FIELDS
    assert not PERSON2_FIELDS & set(payload)
    assert isinstance(payload["investigation_id"], str)
    for key in ("sources", "graph_nodes", "graph_edges"):
        assert isinstance(payload[key], list)


def test_exact_nested_schema(result):
    payload = result.to_contract_dict()
    assert payload["sources"], "mock should contain sources"
    assert payload["graph_edges"], "mock should exercise the edge schema"
    for source in payload["sources"]:
        assert set(source) == SOURCE_FIELDS
    for node in payload["graph_nodes"]:
        assert set(node) == NODE_FIELDS
    for edge in payload["graph_edges"]:
        assert set(edge) == EDGE_FIELDS
        assert set(edge["evidence"]) == EVIDENCE_FIELDS
        assert edge["relationship_type"] in RELATIONSHIPS
    assert set(payload["earliest_observed_source"]) == SOURCE_FIELDS


def test_payload_is_json_serializable_and_round_trips(result):
    text = json.dumps(result.to_contract_dict())
    assert TraceResult.model_validate_json(text) == result


def test_null_date_is_preserved_as_null(result):
    payload = result.to_contract_dict()
    dates = [s["date"] for s in payload["sources"]]
    assert None in dates, "mock must include a source with an unknown date"
    for value in dates:
        assert value is None or (isinstance(value, str) and len(value) == 10)


def test_date_field_is_required_but_nullable():
    base = {"id": "s1", "title": "t", "url": "https://example.org/a", "excerpt": "e"}
    assert Source(**base, date=None).date is None
    with pytest.raises(ValidationError):
        Source(**base)  # omitted date must be stated as null, not defaulted
    with pytest.raises(ValidationError):
        Source(**base, date="sometime last spring")


def test_earliest_observed_source_may_be_null(fixture_data):
    fixture_data["earliest_observed_source"] = None
    parsed = TraceResult.model_validate(fixture_data)
    assert parsed.to_contract_dict()["earliest_observed_source"] is None


def test_earliest_observed_source_must_be_dated(fixture_data):
    undated = next(s for s in fixture_data["sources"] if s["date"] is None)
    fixture_data["earliest_observed_source"] = undated
    with pytest.raises(ValidationError, match="must have a date"):
        TraceResult.model_validate(fixture_data)


def test_earliest_observed_source_must_be_a_listed_source(fixture_data):
    fixture_data["earliest_observed_source"]["id"] = "not-listed"
    with pytest.raises(ValidationError, match="must be one of sources"):
        TraceResult.model_validate(fixture_data)


def test_relationship_types_are_exactly_the_three_allowed():
    assert set(get_args(RelationshipType)) == RELATIONSHIPS


@pytest.mark.parametrize("bad", ["copied_from", "supported", "contradicted", ""])
def test_unknown_relationship_type_is_rejected(fixture_data, bad):
    edge = fixture_data["graph_edges"][0]
    edge["relationship_type"] = bad
    with pytest.raises(ValidationError):
        GraphEdge.model_validate(edge)


def test_edge_without_evidence_is_rejected(fixture_data):
    edge = fixture_data["graph_edges"][0]
    del edge["evidence"]
    with pytest.raises(ValidationError):
        GraphEdge.model_validate(edge)


def test_explicit_citation_requires_observed_hyperlink(fixture_data):
    edge = fixture_data["graph_edges"][0]
    edge["evidence"]["hyperlink"] = None
    with pytest.raises(ValidationError, match="observed hyperlink"):
        GraphEdge.model_validate(edge)


def test_similar_wording_requires_excerpt_and_similarity(fixture_data):
    edge = fixture_data["graph_edges"][0]
    edge["relationship_type"] = "similar_wording"
    with pytest.raises(ValidationError, match="similarity"):
        GraphEdge.model_validate(edge)  # similarity is null in the fixture
    edge["evidence"]["similarity"] = 0.8
    with pytest.raises(ValidationError, match="both quoted excerpts"):
        GraphEdge.model_validate(edge)  # the other page's excerpt is missing
    edge["evidence"]["target_url"] = "https://example.org/mock-article-a"
    edge["evidence"]["target_excerpt"] = "[MOCK] matching text"
    assert GraphEdge.model_validate(edge).evidence.similarity == 0.8
    edge["evidence"]["excerpt"] = "   "
    with pytest.raises(ValidationError):
        GraphEdge.model_validate(edge)


def test_self_edge_is_rejected(fixture_data):
    edge = fixture_data["graph_edges"][0]
    edge["target_id"] = edge["source_id"]
    with pytest.raises(ValidationError, match="itself"):
        GraphEdge.model_validate(edge)


def test_edge_to_unknown_node_is_rejected(fixture_data):
    fixture_data["graph_edges"][0]["target_id"] = "ghost"
    with pytest.raises(ValidationError, match="unknown node ids"):
        TraceResult.model_validate(fixture_data)


def test_zero_edges_is_valid(fixture_data):
    fixture_data["graph_edges"] = []
    assert TraceResult.model_validate(fixture_data).graph_edges == []


def test_unknown_fields_are_rejected(fixture_data):
    with_verdict = copy.deepcopy(fixture_data)
    with_verdict["status"] = "supported"
    with pytest.raises(ValidationError):
        TraceResult.model_validate(with_verdict)
    fixture_data["sources"][0]["author"] = "someone"
    with pytest.raises(ValidationError):
        TraceResult.model_validate(fixture_data)


@pytest.mark.parametrize(
    "url", ["ftp://example.org/a", "javascript:alert(1)", "/relative", "example.org"]
)
def test_non_http_source_url_is_rejected(url):
    with pytest.raises(ValidationError):
        Source(id="s1", title="t", url=url, date=None, excerpt="e")


def test_mock_is_labelled_as_mock(result):
    assert source_tracing.is_mock_mode() is True
    assert result.investigation_id.startswith(MOCK_ID_PREFIX)
    for source in result.sources:
        assert source.title.startswith("[MOCK]")
        assert source.excerpt.startswith("[MOCK]")
        host = source.url.split("/")[2]
        assert host in {"example.org", "example.com", "example.net"}
    for node in result.graph_nodes:
        assert node.title.startswith("[MOCK]")


def test_each_call_returns_an_independent_object():
    first = run(trace_claim("claim one"))
    first.sources.clear()
    second = run(trace_claim("claim two"))
    assert second.sources


@pytest.mark.parametrize("claim", ["", "   ", None, 42])
def test_empty_or_non_string_claim_is_rejected(claim):
    with pytest.raises(ValueError):
        run(trace_claim(claim))


@pytest.mark.parametrize("seed", ["file:///etc/passwd", "not a url", "ftp://x.org/a", ""])
def test_invalid_seed_url_is_rejected(seed):
    with pytest.raises(ValueError):
        run(trace_claim("An illustrative claim", seed))

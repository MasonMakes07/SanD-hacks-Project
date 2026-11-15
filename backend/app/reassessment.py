"""Analyze an unchanged claim with an explicit, server-held evidence set.

Only analyze_claim may make an upstream call here. No retrieval or extraction.
"""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Request

from app.analysis import _persist_safely
from app.config import get_settings
from app.evidence_registry import get_analysis, get_receipt, remember_analysis
from app.gemini_client import analyze_claim
from app.history import error, read_saved_analysis
from app.models import ReassessRequest, StorageMetadata, UpstreamError
from app.validation import clean_edges, clean_nodes, clean_sources, validate_verdict
from source_tracing.urls import dedup_key

router = APIRouter()
MAX_ADDED_SOURCES = 16


class EvidenceSelectionError(ValueError):
    pass


def selected_evidence(original, selections):
    sources = [s.model_copy(deep=True) for s in original.sources]
    nodes = [n.model_copy(deep=True) for n in original.graph_nodes]
    edges = [e.model_copy(deep=True) for e in original.graph_edges]
    by_url = {dedup_key(s.url): s.id for s in sources}
    used_ids = {s.id.lower() for s in sources}
    mapping, seen, added = [], set(), []
    for selection in selections:
        receipt = get_receipt(selection.evidence_set_id)
        if receipt is None or receipt['claim'] != original.claim:
            raise EvidenceSelectionError('Pinned evidence is stale or belongs to a different claim. Search and pin again.')
        receipt_sources = {s.id: s for s in receipt['sources']}
        id_map = {}
        for sid in selection.source_ids:
            if sid not in receipt_sources:
                raise EvidenceSelectionError('A selected source is unknown to its evidence receipt.')
            key = (selection.evidence_set_id, sid)
            if key in seen:
                continue
            seen.add(key)
            source = receipt_sources[sid]
            if len(clean_sources([source])) != 1:
                raise EvidenceSelectionError('A selected source has an unsafe or unsupported URL.')
            url_key = dedup_key(source.url)
            target_id = by_url.get(url_key)
            duplicate = target_id is not None
            if target_id is None:
                target_id = source.id
                if target_id.lower() in used_ids:
                    number = 1
                    while f'r{number}' in used_ids:
                        number += 1
                    target_id = f'r{number}'
                used_ids.add(target_id.lower())
                by_url[url_key] = target_id
                sources.append(source.model_copy(deep=True, update={'id': target_id}))
                added.append(target_id)
                if len(added) > MAX_ADDED_SOURCES:
                    raise EvidenceSelectionError('Select at most sixteen additional sources.')
            id_map[sid] = target_id
            mapping.append({'evidence_set_id': selection.evidence_set_id, 'source_id': sid,
                            'analysis_source_id': target_id, 'duplicate_url': duplicate})
        for node in receipt['nodes']:
            if node.id in id_map:
                nodes.append(node.model_copy(deep=True, update={'id': id_map[node.id]}))
        # Keep relationships only when both endpoints were selected from this receipt.
        # Known-board edges are not reinterpreted after client-supplied ID mappings.
        for edge in receipt['edges']:
            if edge.source_id in id_map and edge.target_id in id_map:
                edges.append(edge.model_copy(deep=True, update={
                    'source_id': id_map[edge.source_id], 'target_id': id_map[edge.target_id]}))
    if not added:
        raise EvidenceSelectionError('No additional unique evidence was selected.')
    nodes = clean_nodes(nodes)
    return clean_sources(sources), nodes, clean_edges(edges, nodes), added, mapping


async def reassess_claim(http_client, settings, original, selections):
    if original.reassessment:
        raise EvidenceSelectionError('Use the original analysis ID for reassessment.')
    sources, nodes, edges, added, mapping = selected_evidence(original, selections)
    evidence_sets = [{'evidence_set_id': item.evidence_set_id,
                      'source_ids': list(dict.fromkeys(item.source_ids)),
                      'provenance': get_receipt(item.evidence_set_id).get('provenance')}
                     for item in selections]
    raw = await analyze_claim(http_client, settings, original.claim, sources)
    verdict = validate_verdict(raw, sources)
    revised = original.model_copy(deep=True)
    revised.analysis_id = uuid4().hex
    revised.checked_at = datetime.now(timezone.utc).isoformat()
    revised.reopened = False
    revised.sources, revised.graph_nodes, revised.graph_edges = sources, nodes, edges
    revised.status, revised.explanation = verdict.status, verdict.explanation
    revised.cited_source_ids, revised.evidence_quotes = verdict.cited_source_ids, verdict.evidence_quotes
    dated = [s for s in sources if s.date]
    revised.earliest_observed_source = min(dated, key=lambda s: s.date) if dated else None
    revised.provenance.saved_analysis = StorageMetadata()
    revised.reassessment = {
        'original_analysis_id': original.analysis_id,
        'original_result': original.model_dump(mode='json'),
        'original_source_ids': [s.id for s in original.sources],
        'added_source_ids': added,
        'source_id_mapping': mapping,
        'accepted_evidence': [item.model_dump() for item in selections],
        'added_evidence_sets': evidence_sets,
        'source_retrieval_performed': False,
        'claim_extraction_performed': False,
        'model_analysis_performed': True,
        'changed': original.status != revised.status,
    }
    await _persist_safely(revised)
    remember_analysis(revised)
    return {'original': original, 'revised': revised,
            'changed': original.status != revised.status}


@router.post('/api/reassess')
async def reassess(body: ReassessRequest, request: Request):
    try:
        original = get_analysis(body.analysis_id)
        if original is None:
            original = await read_saved_analysis(body.analysis_id)
        if original is None:
            return error(404, 'analysis_not_found', 'The original result is unavailable. Reopen it from saved history.')
        if body.claim != original.claim:
            return error(409, 'claim_changed', 'Reassessment must use the unchanged original claim.')
        return await asyncio.wait_for(
            reassess_claim(request.app.state.http_client, get_settings(), original, body.pinned_evidence),
            get_settings().analyze_timeout_seconds)
    except EvidenceSelectionError as exc:
        return error(409, 'invalid_evidence_set', str(exc))
    except (UpstreamError, TimeoutError):
        return error(503, 'reassessment_unavailable', 'Reassessment was unavailable. The original finding is unchanged.')
    except Exception:
        return error(503, 'reassessment_unavailable', 'The original result or reassessment is unavailable. Keep the original finding and try again.')

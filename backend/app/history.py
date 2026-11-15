"""Read saved full results only. No retrieval, extraction, fact-check or Gemini imports."""
import asyncio
import re

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import source_provider
from app.models import AnalyzeResponse, StorageMetadata
from app.Translator import to_error_body

router = APIRouter(prefix='/api/analyses')
RECORD_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def error(code, name, message):
    return JSONResponse(status_code=code, content=to_error_body(name, message))


async def store_read(method, *args):
    store = source_provider._analysis_store()
    if not hasattr(store, method):
        raise RuntimeError('Full-analysis storage is unavailable')
    return await asyncio.wait_for(asyncio.to_thread(getattr(store, method), *args), 2.0)


@router.get('')
async def list_analyses(limit: int = Query(default=20, ge=1, le=50)):
    try:
        items = await store_read('list_analyses', limit)
        # Defensive handling for corrupt/legacy rows; no invented check times.
        items = [item for item in items[:limit] if isinstance(item, dict)
                 and RECORD_ID.fullmatch(str(item.get('analysis_id', '')))
                 and isinstance(item.get('claim'), str)]
        return {'items': items, 'limit': limit}
    except Exception:
        return error(503, 'storage_unavailable', 'Saved investigations are unavailable. New checks still work.')


async def read_saved_analysis(analysis_id):
    if not RECORD_ID.fullmatch(analysis_id):
        return None
    record = await store_read('load_analysis', analysis_id)
    if record is None:
        return None
    result = AnalyzeResponse.model_validate(record['response'])
    result.analysis_id = record['analysis_id']
    result.reopened = True
    result.provenance.saved_analysis = StorageMetadata(
        saved=True, storage_backend=record.get('storage_backend', 'unknown'),
        saved_at=record.get('saved_at'))
    return result


@router.get('/{analysis_id}', response_model=AnalyzeResponse)
async def get_analysis(analysis_id: str):
    try:
        result = await read_saved_analysis(analysis_id)
    except (ValidationError, KeyError, TypeError, ValueError):
        return error(422, 'invalid_saved_result', 'This saved record is incomplete or unreadable. Run a new check.')
    except Exception:
        return error(503, 'storage_unavailable', 'Saved investigations are unavailable. New checks still work.')
    if result is None:
        return error(404, 'analysis_not_found', 'This saved investigation was not found.')
    return result

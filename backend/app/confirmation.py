"""Stateless, signed extraction context: client edits never forge video provenance."""

import base64
import hashlib
import hmac
import json
import time

from app.models import AnalysisInput, ClaimSource, InputContext, InputRejectedError

TOKEN_TTL_SECONDS = 1800


def _fingerprint(capture: AnalysisInput) -> str:
    fields = {key: getattr(capture, key) for key in (
        'text', 'url', 'mode', 'page_title', 'capture_kind', 'video_id', 'video_time_seconds', 'video_duration_seconds'
    )}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def issue_confirmation(capture: AnalysisInput, claim: str, source: ClaimSource | None,
                       context: InputContext, key: str) -> str:
    data = json.dumps({
        'v': 1, 'expires': int(time.time()) + TOKEN_TTL_SECONDS,
        'capture': _fingerprint(capture), 'claim': claim,
        'claim_source': source.model_dump() if source else None,
        'input_context': context.model_dump(),
    }, ensure_ascii=False, separators=(',', ':')).encode()
    encoded = base64.urlsafe_b64encode(data).decode().rstrip('=')
    signature = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f'{encoded}.{signature}'


def approved_context(capture: AnalysisInput, key: str) -> tuple[ClaimSource | None, InputContext]:
    try:
        encoded, signature = capture.confirmation_token.split('.')
        expected = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError('signature')
        data = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        if data['v'] != 1 or data['expires'] < time.time() or data['capture'] != _fingerprint(capture):
            raise ValueError('expired or changed capture')
        source = ClaimSource.model_validate(data['claim_source']) if data['claim_source'] else None
        context = InputContext.model_validate(data['input_context'])
        context.claim_confirmed = True
        context.claim_edited = capture.approved_claim != data['claim']
        return source, context
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise InputRejectedError('Claim confirmation expired or does not match this capture. Start a new check and approve the claim again.') from error

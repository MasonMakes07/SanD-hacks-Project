import asyncio
from dataclasses import replace

import pytest

from app.budget import operation_budget, remaining_timeout
from app.config import get_settings
from app.gemini_client import _hedged_request
from app.models import UpstreamError


async def test_hedge_shares_stage_deadline_and_cleans_both_tasks(monkeypatch):
    attempts, cancelled = [], []
    async def slow(client, settings, body, model, timeout):
        attempts.append((model, timeout))
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(model)
    monkeypatch.setattr('app.gemini_client._request_json', slow)
    settings = replace(get_settings(), gemini_hedge_seconds=0.02)
    start = asyncio.get_running_loop().time()
    with pytest.raises(UpstreamError, match='stage deadline'):
        await _hedged_request(None, settings, {}, 'primary', 0.06)
    assert asyncio.get_running_loop().time() - start < 0.15
    assert len(attempts) == 2
    assert attempts[1][1] < attempts[0][1] - 0.01
    assert len(cancelled) == 2


async def test_parent_deadline_caps_model_stage_and_resets():
    with operation_budget(0.03):
        assert 0 < remaining_timeout(30) <= 0.03
        with operation_budget(90):
            assert remaining_timeout(30) <= 0.03
        await asyncio.sleep(0.04)
        with pytest.raises(TimeoutError):
            remaining_timeout(12)
    assert remaining_timeout(12) == 12


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', 'invalid'])
def test_nonfinite_deadlines_are_not_accepted(monkeypatch, value):
    monkeypatch.setenv('ANALYZE_TIMEOUT_SECONDS', value)
    get_settings.cache_clear()
    assert get_settings().analyze_timeout_seconds == 90


def test_environment_cannot_extend_server_ceiling(monkeypatch):
    for key in ['ANALYZE_TIMEOUT_SECONDS', 'VIDEO_TIMEOUT_SECONDS', 'REQUEST_TIMEOUT_SECONDS']:
        monkeypatch.setenv(key, '999')
    get_settings.cache_clear()
    settings = get_settings()
    assert (settings.analyze_timeout_seconds, settings.video_timeout_seconds, settings.request_timeout_seconds) == (90, 30, 12)

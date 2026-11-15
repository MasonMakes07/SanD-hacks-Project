"""One monotonic deadline shared by all model attempts in an API operation."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar

_deadline: ContextVar[float | None] = ContextVar("investigation_deadline", default=None)


@contextmanager
def operation_budget(seconds: float):
    deadline = asyncio.get_running_loop().time() + seconds
    parent = _deadline.get()
    token = _deadline.set(min(parent, deadline) if parent is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_timeout(stage_seconds: float) -> float:
    deadline = _deadline.get()
    if deadline is None:
        return stage_seconds
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError("Operation deadline exceeded")
    return min(stage_seconds, remaining)

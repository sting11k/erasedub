"""Retry with exponential backoff for providers that call a network service."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from erasedub.context import RunContext

T = TypeVar("T")


def with_retries(
    call: Callable[[], T],
    *,
    retryable: Callable[[Exception], bool],
    ctx: RunContext,
    what: str,
    attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """Run ``call``; on a retryable error wait ``base_delay * 2**n`` (plus jitter) and try again.

    Gives up after ``attempts`` tries and re-raises the last error. Errors for which ``retryable`` returns
    False are raised at once. Cancellation is checked before every try and while waiting.
    """
    for attempt in range(1, attempts + 1):
        ctx.raise_if_cancelled()
        try:
            return call()
        except Exception as exc:
            if attempt >= attempts or not retryable(exc):
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * (1 + random.random() / 4)  # noqa: S311
            ctx.logger.warning(
                "%s failed (%s); retrying in %.1f s (%d/%d)",
                what,
                type(exc).__name__,
                delay,
                attempt,
                attempts - 1,
            )
            _sleep(delay, ctx)
    raise AssertionError("unreachable")  # pragma: no cover


def _sleep(seconds: float, ctx: RunContext) -> None:
    """Sleep in short steps so a cancel request is noticed quickly."""
    deadline = time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        ctx.raise_if_cancelled()
        time.sleep(min(left, 0.5))

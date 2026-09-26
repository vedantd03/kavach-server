"""Optional LangSmith tracing for debugging LLM/OCR calls.

Active only when `langsmith` is installed, LANGSMITH_TRACING=true and LANGSMITH_API_KEY is set;
otherwise every span is a no-op. Callers decide what goes in inputs/outputs: only masked
text may be traced (never raw identifiers, OCR images/text or API keys).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

log = logging.getLogger("detect_core.tracing")
DEFAULT_PROJECT = "kavach-server"


def enabled() -> bool:
    if os.environ.get("LANGSMITH_TRACING", "").lower() not in ("1", "true", "yes"):
        return False
    if not os.environ.get("LANGSMITH_API_KEY"):
        return False
    try:
        import langsmith  # noqa: F401
    except ImportError:
        return False
    return True


class Span:
    """Handle for the current run; safe to use whether tracing is on or off."""

    def __init__(self, run: Any = None):
        self._run = run

    def add_metadata(self, **meta: Any) -> None:
        if self._run is None:
            return
        try:
            self._run.add_metadata(meta)
        except Exception:  # tracing must never break detection
            log.debug("langsmith add_metadata failed")

    def end(self, outputs: dict[str, Any]) -> None:
        if self._run is None:
            return
        try:
            self._run.end(outputs=outputs)
        except Exception:
            log.debug("langsmith end failed")


@contextmanager
def span(name: str, run_type: str = "chain", inputs: Optional[dict[str, Any]] = None,
         metadata: Optional[dict[str, Any]] = None, tags: Optional[list[str]] = None) -> Iterator[Span]:
    if not enabled():
        yield Span()
        return
    from langsmith import trace  # lazy
    with trace(name, run_type=run_type, inputs=inputs or {}, metadata=metadata or {}, tags=tags,  # type: ignore[arg-type]
               project_name=os.environ.get("LANGSMITH_PROJECT", DEFAULT_PROJECT)) as run:
        yield Span(run)


def flush() -> None:
    """Block until queued traces are sent (for short-lived scripts)."""
    if not enabled():
        return
    try:
        from langsmith.run_trees import get_cached_client
        get_cached_client().flush()
    except Exception:
        try:
            from langchain_core.tracers.langchain import wait_for_all_tracers  # type: ignore
            wait_for_all_tracers()
        except Exception:
            log.debug("langsmith flush failed")

"""LangGraph / LangChain agent tools bound to an :class:`S3Memory`.

Design: the tools are ordinary Python callables that return **strings** an LLM
can read directly (eval F1). If ``langchain_core`` is installed they are ALSO
exposed as ``StructuredTool`` objects via :meth:`MemoryToolset.as_langchain_tools`
for drop-in use in a LangGraph agent; if it is absent, everything except that one
method still works, so eval group F runs without langgraph present (Phase 4:
import-guarded, skip gracefully).

Because each toolset closes over one ``S3Memory``, every write goes through that
memory's own agent namespace — a tool cannot write as another agent (eval F3).
"""

from __future__ import annotations

from typing import Callable, Optional

from .client import S3Memory
from .schemas import FindingMemory

try:  # optional dependency
    from langchain_core.tools import StructuredTool

    _HAVE_LANGCHAIN = True
except Exception:  # pragma: no cover - exercised only when langchain absent
    StructuredTool = None  # type: ignore[assignment]
    _HAVE_LANGCHAIN = False


def parse_uri_list(value: str | list[str] | None) -> list[str]:
    """Parse ``"a, b ,c"`` (or a list) into ``["a", "b", "c"]`` — no blanks.

    Splits on commas, strips surrounding whitespace, drops empty fragments
    (eval F2).
    """
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = str(value).split(",")
    return [s.strip() for s in items if s and s.strip()]


class MemoryToolset:
    """A bundle of agent tools writing/reading through one S3Memory."""

    def __init__(self, memory: S3Memory):
        self.memory = memory

    def remember_finding(
        self,
        text: str,
        confidence: float = 0.5,
        related_uris: str = "",
    ) -> str:
        """Persist a finding to durable, object-attached memory.

        ``related_uris`` may be a comma-separated string of S3 URIs. Returns a
        short human-readable confirmation string.
        """
        record = FindingMemory(
            agent_id=self.memory.agent_id,
            text=text,
            confidence=confidence,
            related_uris=parse_uri_list(related_uris),
        )
        name = self.memory.remember(record)
        return (
            f"Saved finding as '{name}' (confidence {confidence}, "
            f"{len(record.related_uris)} related URI(s))."
        )

    def recall_memories(
        self,
        kind: Optional[str] = None,
        max_chars: int = 4000,
    ) -> str:
        """Recall memories attached to the current object, as readable text.

        Returns a plain-text block (never a dict/JSON repr) suitable for pasting
        into an LLM prompt; empty string if there are none.
        """
        text = self.memory.recall_text(
            max_chars=max_chars, kind=kind, include_all_agents=True
        )
        return text if text else "(no memories found on this object)"

    # -- optional LangChain integration ---------------------------------

    def as_callables(self) -> dict[str, Callable[..., str]]:
        """Return the raw callables keyed by tool name."""
        return {
            "remember_finding": self.remember_finding,
            "recall_memories": self.recall_memories,
        }

    def as_langchain_tools(self) -> list:
        """Return LangChain ``StructuredTool`` objects for a LangGraph agent.

        Raises ``RuntimeError`` if ``langchain_core`` is not installed.
        """
        if not _HAVE_LANGCHAIN:
            raise RuntimeError(
                "langchain_core is not installed; install with "
                "`pip install 's3-agent-memory[langgraph]'` to use LangChain tools. "
                "The plain callables (as_callables) work without it."
            )
        return [
            StructuredTool.from_function(
                func=self.remember_finding,
                name="remember_finding",
                description=self.remember_finding.__doc__,
            ),
            StructuredTool.from_function(
                func=self.recall_memories,
                name="recall_memories",
                description=self.recall_memories.__doc__,
            ),
        ]


def build_memory_tools(memory: S3Memory) -> MemoryToolset:
    """Construct a :class:`MemoryToolset` bound to ``memory``'s agent namespace."""
    return MemoryToolset(memory)

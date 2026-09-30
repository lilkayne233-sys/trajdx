"""Adapter layer: heterogeneous agent logs -> one normalized event sequence."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Mapping

from trajdx.schema import Trajectory

#: Framework name -> adapter class, populated by the ``@register_adapter`` decorator.
ADAPTERS: dict[str, type["Adapter"]] = {}


def register_adapter(cls: type["Adapter"]) -> type["Adapter"]:
    ADAPTERS[cls.name] = cls
    return cls


class Adapter(ABC):
    """Converts one framework's log records into :class:`~trajdx.schema.Trajectory`.

    A new framework needs three things: a ``name``, a ``sniff`` that recognises
    its records, and a ``parse`` that emits :class:`AgentStep` objects.
    """

    name: ClassVar[str] = "base"

    @classmethod
    @abstractmethod
    def sniff(cls, payload: Any) -> float:
        """Return confidence in ``[0, 1]`` that this adapter can parse ``payload``."""

    @classmethod
    @abstractmethod
    def parse(cls, payload: Any) -> Trajectory:
        """Convert a raw record into a normalized trajectory."""

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _as_args(raw: Any) -> dict[str, Any]:
        """Tool arguments arrive either as a dict or as a JSON string."""
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {"_raw": raw}
            return parsed if isinstance(parsed, dict) else {"_raw": parsed}
        return {}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def detect_adapter(payload: Any) -> type[Adapter]:
    """Pick the adapter with the highest sniff confidence."""
    if not ADAPTERS:
        raise RuntimeError("no adapters registered")
    scored = sorted(
        ((cls.sniff(payload), cls) for cls in ADAPTERS.values()),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best = scored[0]
    if best_score <= 0:
        raise ValueError(
            f"could not identify the agent framework of this record; "
            f"candidates={[c.name for _, c in scored]}"
        )
    return best


def _iter_records(path: Path):
    """Yield raw records from a ``.jsonl`` dump or a ``.json`` file/array."""
    text = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    if path.suffix.lower() == ".jsonl":
        for line_no, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON line") from exc
        return

    data = json.loads(text)
    if isinstance(data, list):
        yield from data
    else:
        yield data


def load_file(
    path: str | Path,
    framework: str | None = None,
    gold: str | Path | Mapping[str, list[str]] | None = None,
) -> list[Trajectory]:
    """Load every trajectory contained in ``path``.

    ``framework`` forces a specific adapter; by default each record is sniffed.

    ``gold`` is either a sidecar path or an ``instance_id -> files`` mapping.
    When given, matching trajectories receive ``meta["gold_files"]`` so that
    ``localization_failure`` can run.  It is attached here, at the single point
    where trajectories are constructed, so every entry point -- CLI, scripts and
    tests -- gets the behaviour without repeating it.
    """
    path = Path(path)
    forced = ADAPTERS.get(framework) if framework else None

    out: list[Trajectory] = []
    for record in _iter_records(path):
        adapter = forced or detect_adapter(record)
        out.append(adapter.parse(record))

    if gold is not None:
        from trajdx.gold import attach_gold, load_gold

        mapping = load_gold(gold) if isinstance(gold, (str, Path)) else gold
        attach_gold(out, mapping)
    return out


# Import for side effects: each module registers itself with ADAPTERS.
from trajdx.adapters import openhands as _openhands  # noqa: E402,F401
from trajdx.adapters import sweagent as _sweagent  # noqa: E402,F401

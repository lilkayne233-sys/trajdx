"""Adapter layer: heterogeneous agent logs -> one normalized event sequence."""

from __future__ import annotations

import json
import warnings
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
    """Yield ``(position, raw record)`` from a ``.jsonl`` dump or a ``.json`` file.

    ``.jsonl`` is streamed line by line on purpose.  The published corpora are
    gigabytes, and reading one into a single string before splitting it turns a
    load that should work into a ``MemoryError``: a 1 GB dump needs more than
    1 GB to hold the text, then more again for the lines.
    """
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, 1):
                # Only the first line can carry the annotation tool's BOM.
                text = line.lstrip("\ufeff") if line_no == 1 else line
                text = text.strip()
                if not text:
                    continue
                try:
                    yield line_no, json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_no}: invalid JSON line") from exc
        return

    text = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    data = json.loads(text)
    for index, item in enumerate(data if isinstance(data, list) else [data], 1):
        yield index, item


def load_file(
    path: str | Path,
    framework: str | None = None,
    gold: str | Path | Mapping[str, list[str]] | None = None,
    on_empty: str = "warn",
) -> list[Trajectory]:
    """Load every trajectory contained in ``path``.

    ``framework`` forces a specific adapter; by default each record is sniffed.

    ``gold`` is either a sidecar path or an ``instance_id -> files`` mapping.
    When given, matching trajectories receive ``meta["gold_files"]`` so that
    ``localization_failure`` can run.  It is attached here, at the single point
    where trajectories are constructed, so every entry point -- CLI, scripts and
    tests -- gets the behaviour without repeating it.

    ``on_empty`` decides what to do about a record that parses to **zero steps**.
    That is almost always an adapter meeting a format it does not understand,
    and returning it would read downstream as "the agent did nothing" -- which
    is how a whole corpus of SWE-agent rows once came back as 6,670 empty runs
    with no error.  ``"warn"`` (the default) skips it and warns once with the
    positions; ``"error"`` refuses the file outright, as
    ``detect_adapter`` already does for an unrecognised record.
    """
    if on_empty not in ("warn", "error"):
        raise ValueError(f"on_empty must be 'warn' or 'error', not {on_empty!r}")

    path = Path(path)
    forced = ADAPTERS.get(framework) if framework else None

    out: list[Trajectory] = []
    empty: list[int] = []
    for position, record in _iter_records(path):
        try:
            adapter = forced or detect_adapter(record)
        except ValueError as exc:
            raise ValueError(f"{path}:{position}: {exc}") from exc
        trajectory = adapter.parse(record)
        if not trajectory.steps:
            if on_empty == "error":
                raise ValueError(
                    f"{path}:{position}: the {adapter.name!r} adapter parsed this "
                    "record into zero steps. That is a format mismatch, not an "
                    "empty run. Use on_empty='warn' to skip such records, or "
                    "--framework to force the right adapter."
                )
            empty.append(position)
            continue
        out.append(trajectory)

    if empty:
        shown = ", ".join(str(p) for p in empty[:5])
        more = "" if len(empty) <= 5 else f" (+{len(empty) - 5} more)"
        warnings.warn(
            f"{path}: skipped {len(empty)} record(s) that parsed to zero steps "
            f"(lines {shown}{more}). Zero steps means the adapter did not "
            "understand the record, not that the run was empty.",
            RuntimeWarning,
            stacklevel=2,
        )

    if gold is not None:
        from trajdx.gold import attach_gold, load_gold

        mapping = load_gold(gold) if isinstance(gold, (str, Path)) else gold
        attach_gold(out, mapping)
    return out


# Import for side effects: each module registers itself with ADAPTERS.
from trajdx.adapters import openhands as _openhands  # noqa: E402,F401
from trajdx.adapters import sweagent as _sweagent  # noqa: E402,F401

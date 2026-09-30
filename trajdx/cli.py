"""``trajdx`` command line interface.

    trajdx replay    <file> --instance django__django-12345
    trajdx diagnose  <file> --out report.json
    trajdx findings  <file> --out to_label.jsonl
    trajdx export    <file> --out stats.csv --format csv
    trajdx detectors
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Iterable, Optional, Sequence

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from trajdx import report as render
from trajdx.adapters import ADAPTERS, load_file
from trajdx.detectors import REGISTRY, detect_all, filter_findings
from trajdx.fingerprints import clean_output
from trajdx.metrics import AggregateReport, WasteReport, aggregate, wasted_step_ratio
from trajdx.schema import StepKind, Trajectory

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Step-level failure diagnosis for code-agent trajectories.",
)
console = Console()

#: Detectors are gated by tier.
#:
#: ``replay`` shows individual claims as conclusions, so it defaults to the rules
#: that survived annotation.  ``diagnose`` and ``export`` produce aggregate
#: statistics that need volume to mean anything, and they already disclose
#: per-category discrimination in their output -- so they default to every rule
#: and let the numbers say which ones are weak.
DEFAULT_TIER = "core"
AGGREGATE_TIER = "all"


def _tier_value(raw: str) -> Optional[str]:
    """``all`` disables the gate; anything else names a tier."""
    value = (raw or DEFAULT_TIER).lower()
    return None if value == "all" else value


# --------------------------------------------------------------------------
# Shared plumbing
# --------------------------------------------------------------------------


def _diagnose(
    trajectories: Iterable[Trajectory],
    only: Sequence[str] | None = None,
    min_confidence: float = 0.0,
    outcome: str | None = None,
    tier: str = DEFAULT_TIER,
) -> list[tuple[Trajectory, list, WasteReport]]:
    """Run the full pipeline over a set of trajectories."""
    results = []
    for trajectory in trajectories:
        if outcome == "resolved" and trajectory.resolved is not True:
            continue
        if outcome == "unresolved" and trajectory.resolved is not False:
            continue
        findings = filter_findings(
            detect_all(trajectory, only=only, tier=_tier_value(tier)),
            min_confidence=min_confidence,
        )
        results.append((trajectory, findings, wasted_step_ratio(trajectory, findings)))
    return results


def _compact(text: str | None, limit: int) -> str:
    """Collapse whitespace, then shorten to ``limit`` keeping both ends.

    Observation bodies are mostly newlines and indentation; leaving them in makes
    JSON escaping triple the size of the annotation file for no added meaning.

    The shortening keeps the **head and the tail**, not just the head.  Both ends
    carry the evidence and they are different evidence: the input and the command
    echo sit at the top, the traceback, the failing assertion and the exit status
    sit at the bottom.  Cutting after ``limit`` characters threw away exactly the
    part an annotator needs -- several pilot findings had observations whose first
    800 characters were nothing but the pytest session banner, with the error text
    below the cut, which left the verdict resting on a fingerprint the annotator
    could not see.
    """
    cleaned = clean_output(text)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n+", "\n", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) <= limit or limit <= 0:
        return cleaned

    head = limit * 2 // 3
    tail = limit - head
    elided = len(cleaned) - limit
    return f"{cleaned[:head]}\n… [{elided} chars elided] …\n{cleaned[-tail:]}"


def _load(
    data: Path,
    framework: str | None,
    limit: int | None = None,
    gold: Path | None = None,
) -> list[Trajectory]:
    if framework and framework not in ADAPTERS:
        console.print(f"[red]unknown framework '{framework}'[/]; known: {sorted(ADAPTERS)}")
        raise typer.Exit(code=2)
    try:
        trajectories = load_file(data, framework=framework, gold=gold)
    except Exception as exc:  # surface the parse failure as a CLI error, not a traceback
        console.print(f"[red]failed to load {data}:[/] {exc}")
        raise typer.Exit(code=2) from exc
    return trajectories[:limit] if limit else trajectories


#: Shared option so every command that can use gold data spells it the same way.
GOLD_OPTION = typer.Option(
    None,
    "--gold",
    exists=True,
    dir_okay=False,
    help="gold patch sidecar (JSONL); enables the localization_failure rule",
)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@app.command()
def replay(
    data: Path = typer.Argument(..., exists=True, dir_okay=False, help="trajectory file"),
    instance: Optional[str] = typer.Option(None, "--instance", "-i", help="instance id to replay"),
    index: int = typer.Option(0, "--index", "-n", help="position in the file when --instance is absent"),
    framework: Optional[str] = typer.Option(None, "--framework", "-f", help="force an adapter"),
    max_steps: int = typer.Option(0, "--max-steps", help="truncate the step table (0 = show all)"),
    min_confidence: float = typer.Option(0.0, "--min-confidence", help="hide weaker findings"),
    only: Optional[list[str]] = typer.Option(None, "--only", help="run only these detectors"),
    tier: str = typer.Option(DEFAULT_TIER, "--tier", help="core | experimental | all"),
    gold: Optional[Path] = GOLD_OPTION,
) -> None:
    """Replay one trajectory step by step, with wasted steps flagged."""
    trajectories = _load(data, framework, gold=gold)
    if not trajectories:
        console.print("[red]no trajectories found[/]")
        raise typer.Exit(code=1)

    target: Trajectory | None = None
    if instance:
        target = next((t for t in trajectories if t.instance_id == instance), None)
        if target is None:
            console.print(f"[red]instance '{instance}' not found in {data}[/]")
            raise typer.Exit(code=1)
    else:
        if not 0 <= index < len(trajectories):
            console.print(f"[red]index {index} out of range (0-{len(trajectories) - 1})[/]")
            raise typer.Exit(code=1)
        target = trajectories[index]

    findings = filter_findings(
        detect_all(target, only=only, tier=_tier_value(tier)), min_confidence=min_confidence
    )
    report = wasted_step_ratio(target, findings)
    render.render_trajectory(
        target, findings, report, console, max_rows=max_steps or None
    )


@app.command()
def diagnose(
    data: Path = typer.Argument(..., exists=True, dir_okay=False, help="trajectory file"),
    framework: Optional[str] = typer.Option(None, "--framework", "-f"),
    limit: Optional[int] = typer.Option(None, "--limit", help="analyse only the first N"),
    min_confidence: float = typer.Option(0.0, "--min-confidence"),
    only: Optional[list[str]] = typer.Option(None, "--only", help="run only these detectors"),
    tier: str = typer.Option(AGGREGATE_TIER, "--tier", help="core | experimental | all"),
    outcome: Optional[str] = typer.Option(
        None, "--outcome", help="restrict to resolved or unresolved runs"
    ),
    out: Optional[Path] = typer.Option(None, "--out", help="write the aggregate report as JSON"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="skip the console tables"),
    gold: Optional[Path] = GOLD_OPTION,
) -> None:
    """Diagnose a whole corpus and print the aggregate breakdown."""
    trajectories = _load(data, framework, limit, gold=gold)
    if gold:
        # Coverage is reported because an unattached sidecar and a rule that
        # simply found nothing look identical in the output otherwise.
        from trajdx.gold import coverage

        console.print(f"[dim]gold coverage: {coverage(trajectories)}[/]")
    results = _diagnose(
        trajectories, only=only, min_confidence=min_confidence, outcome=outcome, tier=tier
    )

    if not results:
        console.print("[yellow]nothing matched[/]")
        raise typer.Exit(code=1)

    reports = [r for _, _, r in results]
    aggregate_report = aggregate(reports)

    if not quiet:
        for table in render.aggregate_tables(aggregate_report):
            console.print(table)

    if out:
        payload = aggregate_report.to_dict()
        payload["n_analysed"] = len(results)
        payload["config"] = {
            "only": list(only) if only else None,
            "min_confidence": min_confidence,
            "outcome": outcome,
            "tier": tier,
            "source": str(data),
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"[green]wrote[/] {out}")


@app.command()
def export(
    data: Path = typer.Argument(..., exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out", "-o", help="output path"),
    fmt: str = typer.Option("jsonl", "--format", help="jsonl | csv | json"),
    framework: Optional[str] = typer.Option(None, "--framework", "-f"),
    limit: Optional[int] = typer.Option(None, "--limit"),
    min_confidence: float = typer.Option(0.0, "--min-confidence"),
    only: Optional[list[str]] = typer.Option(None, "--only"),
    tier: str = typer.Option("all", "--tier", help="core | experimental | all"),
    with_findings: bool = typer.Option(True, "--findings/--no-findings"),
    gold: Optional[Path] = GOLD_OPTION,
) -> None:
    """Dump per-trajectory diagnostics for downstream analysis."""
    trajectories = _load(data, framework, limit, gold=gold)
    results = _diagnose(trajectories, only=only, min_confidence=min_confidence, tier=tier)
    records = [r.to_dict(with_findings=with_findings) for _, _, r in results]

    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        with out.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    elif fmt == "json":
        out.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    elif fmt == "csv":
        # Flatten to the columns that matter for spreadsheets; findings stay in JSON.
        fieldnames = [
            "instance_id", "framework", "resolved", "total_steps",
            # Process shape travels with the waste columns: it is the part of the
            # export that actually separates passing runs from failing ones.
            "source_edits", "test_runs", "tests_per_source_edit", "test_run_ratio",
            "wasted_steps", "wasted_step_ratio", "n_findings",
            *sorted({c for r in records for c in r["by_category"]}),
        ]
        with out.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                row = dict(record)
                row.update(record.get("by_category", {}))
                writer.writerow(row)
    else:
        console.print(f"[red]unknown format '{fmt}'[/] (expected jsonl, json or csv)")
        raise typer.Exit(code=2)

    console.print(f"[green]wrote[/] {len(records)} records -> {out}")


@app.command()
def findings(
    data: Path = typer.Argument(..., exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out", "-o", help="annotation file (JSONL)"),
    framework: Optional[str] = typer.Option(None, "--framework", "-f"),
    limit: Optional[int] = typer.Option(None, "--limit"),
    per_detector: int = typer.Option(40, "--per-detector", help="sample at most N per detector"),
    context: int = typer.Option(3, "--context", help="steps of context on each side"),
    obs_chars: int = typer.Option(1200, "--obs-chars", help="observation truncation"),
    seed: int = typer.Option(0, "--seed"),
    gold: Optional[Path] = GOLD_OPTION,
) -> None:
    """Sample findings with step context, ready for LLM pre-labelling and review."""
    import random

    rng = random.Random(seed)
    trajectories = _load(data, framework, limit, gold=gold)

    buckets: dict[str, list[tuple[Trajectory, object]]] = {}
    for trajectory in trajectories:
        for finding in detect_all(trajectory):
            buckets.setdefault(finding.detector, []).append((trajectory, finding))

    rows = []
    for detector, items in sorted(buckets.items()):
        rng.shuffle(items)
        for trajectory, finding in items[:per_detector]:
            lo = max(0, finding.start - context)
            hi = min(trajectory.n_steps - 1, finding.end + context)
            rows.append(
                {
                    "finding_id": f"{trajectory.instance_id}|{detector}|{finding.start}",
                    "instance_id": trajectory.instance_id,
                    "resolved": trajectory.resolved,
                    "detector": detector,
                    "category": finding.category.value,
                    "phase": finding.phase.value,
                    "severity": finding.severity.value,
                    "confidence": finding.confidence,
                    "evidence": finding.evidence,
                    "wasted_steps": list(finding.wasted_steps),
                    "detail": finding.detail,
                    "context": [
                        {
                            "idx": i,
                            "kind": trajectory.steps[i].kind.value,
                            "action": trajectory.steps[i].action_summary(160),
                            # Enough detail for an annotator to check the
                            # detector's identity claims: which lines were viewed,
                            # how large the edit was, which file it hit.
                            "path": (
                                trajectory.steps[i].files_touched[0]
                                if trajectory.steps[i].files_touched
                                else None
                            ),
                            "view_range": trajectory.steps[i].args.get("view_range"),
                            "edit_bytes": (
                                len(
                                    str(
                                        trajectory.steps[i].args.get("new_str")
                                        or trajectory.steps[i].args.get("file_text")
                                        or ""
                                    )
                                )
                                or None
                            ),
                            "edit_verb": trajectory.steps[i].args.get("verb"),
                            "observation": _compact(trajectory.steps[i].observation, obs_chars),
                            "error_kind": trajectory.steps[i].error_kind,
                            "is_wasted": i in set(finding.wasted_steps),
                        }
                        for i in range(lo, hi + 1)
                    ],
                }
            )

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts = {k: min(len(v), per_detector) for k, v in sorted(buckets.items())}
    console.print(f"[green]wrote[/] {len(rows)} findings -> {out}")
    for detector, count in counts.items():
        console.print(f"  {detector:24} {count}")


@app.command()
def detectors() -> None:
    """List the registered detectors and what they look for."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("detector")
    table.add_column("tier")
    table.add_column("category")
    table.add_column("phase")
    table.add_column("doc", overflow="fold")
    for name, cls in sorted(REGISTRY.items()):
        doc = (cls.__doc__ or "").strip().split("\n")[0]
        table.add_row(name, cls.tier.value, cls.category.value, cls.phase.value, doc)
    console.print(table)
    console.print(
        Text(
            f"default tier: {DEFAULT_TIER}  (use --tier all to include experimental rules)",
            style="dim",
        )
    )


@app.command()
def adapters() -> None:
    """List the registered trajectory adapters."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("framework")
    table.add_column("doc", overflow="fold")
    for name, cls in sorted(ADAPTERS.items()):
        table.add_row(name, (cls.__doc__ or "").strip().split("\n")[0])
    console.print(table)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

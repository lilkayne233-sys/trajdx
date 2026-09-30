"""Console rendering: turn diagnoses into something a human can read.

The replay view is the point of the tool.  A SWE-bench score tells you *that* a
run failed; this renders the step-by-step record with the wasted steps lit up
and the reason attached, so the failure is legible in one screen.
"""

from __future__ import annotations

from typing import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from trajdx.detectors.base import Category, Finding, Severity
from trajdx.metrics import (
    AggregateReport,
    OutcomeStats,
    WasteReport,
)
from trajdx.schema import StepKind, Trajectory

_SEVERITY_STYLE = {
    Severity.HIGH: "bold red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "dim",
}

_KIND_STYLE = {
    StepKind.READ: "cyan",
    StepKind.EDIT: "magenta",
    StepKind.SHELL: "blue",
    StepKind.SEARCH: "cyan",
    StepKind.SUBMIT: "green",
    StepKind.THOUGHT: "dim",
    StepKind.PLAN: "dim",
}


def waste_index(findings: Sequence[Finding]) -> dict[int, Finding]:
    """Map each wasted step to the finding that explains it (highest severity wins)."""
    order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
    index: dict[int, Finding] = {}
    for finding in sorted(findings, key=lambda f: order.get(f.severity, 3)):
        for step in finding.wasted_steps:
            index.setdefault(step, finding)
    return index


# --------------------------------------------------------------------------
# Single trajectory
# --------------------------------------------------------------------------


def summary_panel(trajectory: Trajectory, report: WasteReport) -> Panel:
    outcome = (
        "unknown"
        if trajectory.resolved is None
        else ("RESOLVED" if trajectory.resolved else "UNRESOLVED")
    )
    outcome_style = {
        "RESOLVED": "bold green",
        "UNRESOLVED": "bold red",
        "unknown": "dim",
    }[outcome]

    title = Text()
    title.append(f"{trajectory.instance_id}", style="bold")
    title.append("  ·  ")
    title.append(trajectory.framework, style="cyan")
    title.append("  ·  ")
    title.append(outcome, style=outcome_style)

    body = Text()
    body.append(f"{report.total_steps} steps", style="bold")
    body.append("   wasted ")
    body.append(f"{report.wasted_steps}", style="bold yellow")
    body.append(f" ({report.ratio:.1%})")
    body.append(f"   {report.n_findings} finding(s)")

    # Process shape, shown next to waste on purpose: waste does not separate
    # outcomes (AUC 0.52), verification intensity does (AUC 0.62 inverted).
    body.append(
        f"\n{report.source_edits} source edit(s) · {report.test_runs} test run(s) · "
        f"{report.tests_per_source_edit:.2f} test(s) per source edit",
        style="dim",
    )
    if trajectory.exit_status:
        body.append(f"\nexit: {trajectory.exit_status}", style="dim")

    phases = ", ".join(f"{k} {v}" for k, v in sorted(report.by_phase.items()))
    if phases:
        body.append(f"\nwaste by phase: {phases}", style="dim")

    return Panel(body, title=title, border_style="blue", expand=False)


def step_table(
    trajectory: Trajectory,
    findings: Sequence[Finding],
    max_rows: int | None = None,
) -> Table:
    """Render the trajectory as a replay table with wasted steps flagged."""
    index = waste_index(findings)

    table = Table(
        show_header=True,
        header_style="bold",
        box=None,
        pad_edge=False,
        expand=True,
    )
    table.add_column("step", justify="right", style="dim", width=5)
    table.add_column("kind", width=7)
    table.add_column("action", overflow="ellipsis", no_wrap=True, ratio=4)
    table.add_column("result", width=18, overflow="ellipsis", no_wrap=True)
    table.add_column("waste", width=22, overflow="ellipsis", no_wrap=True)

    steps = list(enumerate(trajectory.steps))
    if max_rows is not None and len(steps) > max_rows:
        # Keep the head and the tail: setup at the start, the submission at the end.
        half = max_rows // 2
        steps = steps[:half] + [(-1, None)] + steps[-half:]  # type: ignore[list-item]

    for idx, step in steps:
        if step is None:
            table.add_row("…", "…", "…", "", "")
            continue

        kind_style = _KIND_STYLE.get(step.kind, "")
        result = "ok"
        result_style = "green"
        if step.error_kind:
            result = step.error_kind
            result_style = "red"
        elif step.exit_code not in (None, 0):
            result = f"exit {step.exit_code}"
            result_style = "red"

        finding = index.get(idx)
        waste = Text("")
        if finding is not None:
            waste.append("● ", style=_SEVERITY_STYLE.get(finding.severity, ""))
            waste.append(finding.category.value, style=_SEVERITY_STYLE.get(finding.severity, ""))

        table.add_row(
            str(idx),
            Text(step.kind.value, style=kind_style),
            Text(step.action_summary(110)),
            Text(result[:40], style=result_style),
            waste,
        )
    return table


def findings_table(findings: Sequence[Finding], title: str = "Findings") -> Table:
    table = Table(title=title, show_header=True, header_style="bold", expand=True)
    table.add_column("severity", width=8)
    table.add_column("category", width=22)
    table.add_column("phase", width=12)
    table.add_column("steps", width=12, justify="right")
    table.add_column("wasted", width=7, justify="right")
    table.add_column("evidence", overflow="fold", ratio=1)

    for finding in findings:
        span = f"{finding.start}-{finding.end}" if finding.start != finding.end else str(finding.start)
        table.add_row(
            Text(finding.severity.value, style=_SEVERITY_STYLE.get(finding.severity, "")),
            finding.category.value,
            finding.phase.value,
            span,
            str(finding.n_wasted),
            finding.evidence,
        )
    if not findings:
        table.add_row("", "", "", "", "", Text("no findings", style="dim"))
    return table


def render_findings(findings: Sequence[Finding], console: Console) -> None:
    """Print findings as a list, so long evidence is never truncated away."""
    console.print(Text("\nFindings", style="bold"))
    if not findings:
        console.print(Text("  no failure signatures detected", style="dim"))
        return
    for finding in findings:
        span = (
            f"{finding.start}-{finding.end}"
            if finding.start != finding.end
            else str(finding.start)
        )
        header = Text()
        header.append("  ● ", style=_SEVERITY_STYLE.get(finding.severity, ""))
        header.append(f"{finding.severity.value.upper():6}", style=_SEVERITY_STYLE.get(finding.severity, ""))
        header.append(f" {finding.category.value:22}", style="bold")
        header.append(f" {finding.phase.value:12}", style="dim")
        header.append(f" steps {span}")
        header.append(f"  ·  {finding.n_wasted} wasted", style="yellow")
        header.append(f"  ·  conf {finding.confidence:.2f}", style="dim")
        console.print(header)

        evidence = Text("      ")
        evidence.append(finding.evidence, style="dim")
        console.print(evidence)


def render_trajectory(
    trajectory: Trajectory,
    findings: Sequence[Finding],
    report: WasteReport,
    console: Console,
    max_rows: int | None = None,
    show_steps: bool = True,
) -> None:
    console.print(summary_panel(trajectory, report))
    if show_steps:
        console.print(step_table(trajectory, findings, max_rows=max_rows))
    render_findings(findings, console)


# --------------------------------------------------------------------------
# Corpus level
# --------------------------------------------------------------------------


def outcome_table(stats: OutcomeStats) -> Table:
    table = Table(title=stats.label, show_header=True, header_style="bold")
    table.add_column("metric", overflow="fold")
    table.add_column("value", justify="right")
    table.add_row("runs", str(stats.n))
    table.add_row("mean steps", f"{stats.mean_steps:.1f}")
    # Process shape leads, waste follows.  The ordering mirrors the measured
    # discrimination: total_steps 0.69 and tests-per-source-edit 0.62 inverted
    # carry signal, mean WSR at 0.52 does not.
    table.add_row("mean source edits", f"{stats.mean_source_edits:.1f}")
    table.add_row("mean test runs", f"{stats.mean_test_runs:.1f}")
    table.add_row("mean tests per source edit", f"{stats.mean_tests_per_source_edit:.2f}")
    table.add_row("mean test run ratio", f"{stats.mean_test_run_ratio:.1%}")
    table.add_row("mean wasted steps", f"{stats.mean_wasted:.1f}")
    table.add_row("mean WSR", f"{stats.mean_ratio:.1%}")
    for category in sorted(stats.category_rate):
        rate = stats.category_rate[category]
        table.add_row(
            f"  {category}",
            f"{rate:.1%}  ({stats.category_waste.get(category, 0.0):.1f} steps)",
        )
    return table


def aggregate_tables(aggregate: AggregateReport) -> list[Table]:
    """Build the corpus-level tables, one per outcome group plus a lift table."""
    tables = [outcome_table(aggregate.overall)]
    for label in ("resolved", "unresolved", "unknown"):
        if label in aggregate.by_outcome:
            tables.append(outcome_table(aggregate.by_outcome[label]))

    lift = aggregate.category_lift()
    if lift:
        table = Table(
            title="discrimination: unresolved rate − resolved rate",
            show_header=True,
            header_style="bold",
        )
        table.add_column("category", overflow="fold")
        table.add_column("Δ rate", justify="right")
        table.add_column("verdict")
        for category, delta in sorted(lift.items(), key=lambda kv: -kv[1]):
            if delta >= 0.10:
                verdict = Text("discriminates", style="green")
            elif delta >= 0.03:
                verdict = Text("weak", style="yellow")
            else:
                verdict = Text("no signal", style="dim red")
            table.add_row(category, f"{delta:+.1%}", verdict)
        tables.append(table)
    return tables

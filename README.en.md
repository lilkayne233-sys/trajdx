# trajdx

**Step-level failure diagnosis for code-agent trajectories.**

Language: [中文](README.md) · [English](README.en.md)

SWE-bench-style evaluation gives an agent one bit per task — resolved or not. That
score tells you *that* an agent failed, never *where* or *why*. `trajdx` turns raw
agent logs into a normalised event sequence, runs pure-Python rule detectors over
that sequence, and reports the specific step ranges where an agent looped, searched
blindly, or stopped verifying its own work.

No LLM in the detection path. No network. No Docker. Detection runs in ~3 ms per
trajectory.

---

## What it does

| Stage | Module | Output |
|---|---|---|
| Normalise | `trajdx.adapters` | `Trajectory` of `AgentStep` |
| Fingerprint errors | `trajdx.fingerprints` | 24 error kinds + exit codes |
| Detect | `trajdx.detectors` | `Finding` objects with step ranges and evidence |
| Quantify waste | `trajdx.metrics` | Wasted Step Ratio, per-category attribution |
| Report | `trajdx.report`, `trajdx.cli` | replay / diagnose / export / findings |

One `AgentStep` is a decision–action–observation triple. Each step carries three
identity keys:

- `action_key` — what the agent *meant* to do, with volatile tokens (paths, ids,
  line numbers) scrubbed, so `pytest tests/test_x.py` and `pytest tests/test_y.py`
  collapse together.
- `exact_key` — the literal action including its payload, so two edits to the same
  file are only "the same" if the replacement text matches too.
- `observation_key` — an identity for the *result*, hashed over the whole cleaned
  observation body.

The gap between `action_key` and `exact_key` is what makes loop detection honest:
repeating an action is not a failure if the agent changed its input in between.

---

## Quickstart

```bash
python -m trajdx.cli detectors                       # what rules exist
python -m trajdx.cli adapters                        # what log formats are supported
python -m trajdx.cli replay data/raw/openhands_sample.jsonl --index 0
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl
python -m trajdx.cli export data/raw/openhands_sample.jsonl --out data/reports/diag.jsonl
python -m trajdx.cli findings data/raw/openhands_sample.jsonl --out data/labels/to_label.jsonl
```

Install for development:

```bash
pip install -e ".[dev]"
pytest -q
```

---

## Detectors and measured precision

Rules were built against 300 real OpenHands trajectories (150 resolved / 150
unresolved) and calibrated with **350 LLM-pre-labelled findings reviewed by hand**
across three annotation rounds. The numbers below are from the held-out v3 round
(83 findings that joined cleanly to labels).

| Detector | Tier | Precision | n |
|---|---|---|---|
| `redundant_read` | **core** | 100% | 4 |
| `termination_anomaly` | **core** | 91.7% | 24 |
| `execution_loop` | experimental | 66.7% | 3 |
| `verification_gap` | experimental | 53.8% | 13 |
| `weak_verification` | experimental | 18.2% | 11 |
| `blind_search` | experimental | 0% | 1 |
| `environment_stuck` | experimental | — | 1 |
| `localization_failure` | experimental | — | 0 |
| **overall** | | **45.8%** | **83** |

**Only two detectors clear the 88% precision bar.** Every other rule is shipped
but marked `Tier.EXPERIMENTAL`, and `replay`/`findings` exclude experimental rules
by default — an individual experimental finding should not be read as a
conclusion. Use `--tier all` when you want volume for aggregate analysis, where the
per-category discrimination is disclosed alongside.

This is the honest result of the annotation loop, not a target that was hit. The
rules that matched their hypothesis were kept and promoted; the rest are labelled
as unproven rather than quietly tuned until the number looked good.

### Notes on individual rules

- **`execution_loop`** has a sharp failure mode. Loops detected *with* an
  intervening edit were valid only 3.4% of the time (n=29); loops *without* one
  were valid 66.7% of the time (n=3). Gating on "no intervening edit" cut volume
  from 148 findings to 5 across the corpus, and is why this rule now fires rarely.
  Repeating an action after changing the input is usually *legitimate* debugging.
- **`localization_failure`** compares the agent's patch against the gold patch's
  file set. Without `meta["gold_files"]` it stays silent by design rather than
  guessing from heuristics.
- **`weak_verification`** and **`verification_gap`** charge **zero wasted steps**.
  A coverage gap is a diagnostic signal, not proof that the steps themselves were
  wasted.

---

## Wasted Step Ratio

`WSR` charges every flagged step to exactly one waste category by severity, so
`WSR <= 1.0` always holds and no step is double-counted.

```bash
python scripts/detector_profile.py     # per-detector volume and step coverage
python scripts/discrimination.py       # AUC of each metric against the resolved label
```

**WSR does not predict failure.** Mann-Whitney AUC against the resolved/unresolved
label is **0.520** — indistinguishable from chance. Reporting it as a failure
predictor would be a mistake, so the docs and CLI describe it strictly as an
*efficiency* metric: it says how much of a run was spent re-treading ground, not
whether the run was going to succeed.

The metrics that do carry signal are process-shape metrics:

| Metric | AUC | Resolved | Unresolved |
|---|---|---|---|
| `total_steps` | 0.694 | 58.8 | 71.4 |
| `tests_per_source_edit` | 0.342 → 0.658 inverted | 0.88 | 0.66 |
| `test_run_ratio` | 0.350 → 0.650 inverted | — | — |
| `source_edits` | — | 9.6 | 12.5 |
| `wasted_step_ratio` | 0.520 | — | — |

Resolved runs test *more per edit*. Unresolved runs make more edits and run longer.
The strongest actionable signal in the corpus is verification intensity, not waste.

---

## Data and adapters

`data/raw/openhands_sample.jsonl` holds 300 trajectories sampled from a pool of
67,074 (150 resolved / 150 unresolved, 3 duplicate `instance_id`s left in place
rather than silently deduplicated).

Corpus facts worth knowing before writing a rule:

- `exit_status` is `submit` for 263 runs and `RuntimeError: Agent reached maximum
  iteration (100)` for 37.
- Mean edits per trajectory is **11.04**, but mean *source* edits is **3.70** —
  **67% of all edits target scratch or test files**. Any rule that counts "edits"
  without filtering will be dominated by noise.
- Tool-call mix: `execute_bash` 9865, `str_replace_editor` 8343, `think` 801,
  `task_tracker` 264, `finish` 263.
- Assumption *not* present in this data: no assistant message ever issues multiple
  tool calls at once, and `model_patch` is never empty.

Adapters: `openhands` (pairs a `tool_call` with its `tool` result by
`tool_call_id`) and `sweagent` (resolves `edit` targets against
`state["open_file"]`). Both classify an error only when the step kind can actually
carry one, so error fingerprints are never scraped out of file contents.

---

## Repository layout

```
trajdx/
  schema.py           AgentStep / Trajectory, keys, patch_files()
  fingerprints.py     24 error kinds, exit-code extraction, fast pre-filter
  heuristics.py       source-vs-scratch classification, test/setup detection
  adapters/           openhands.py, sweagent.py
  detectors/          base.py (registry, tiers), execution_loop, localization,
                      verification, termination, environment
  metrics.py          WSR attribution, aggregates, category lift
  report.py           rich rendering
  cli.py              replay / diagnose / export / findings / detectors / adapters
tests/                119 tests
scripts/              fetch_trajectories, detector_profile, discrimination, evaluate
docs/annotation_guide.md    the labelling rubric used for all three rounds
data/labels/          raw LLM pre-labels + human review verdicts
```

## Re-running the evaluation

```bash
python scripts/evaluate.py \
  --findings data/labels/to_label_v3.jsonl \
  --labels "data/labels/labelled_v3_*.jsonl"
```

Label files are read with `utf-8-sig` because the annotation pass writes a BOM.
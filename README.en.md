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
unresolved) and calibrated with LLM-pre-labelled findings reviewed by hand across
several annotation rounds. The table below is **not hand-written**: it is emitted
by the command shown, and reproduces exactly.

```bash
python scripts/evaluate.py --labels "data/labels/labelled_v3_*.jsonl" --markdown
```

| Detector | Tier | Precision | n |
|---|---|---|---|
| `execution_loop` | experimental | 100.0% | 1 |
| `termination_anomaly` | core | 91.7% | 24 |
| `weak_verification` | experimental | 66.7% | 3 |
| `verification_gap` | experimental | 40.0% | 5 |
| `blind_search` | experimental | 0.0% | 1 |
| `environment_stuck` | experimental | — | 0 |
| `localization_failure` | experimental | — | 0 |
| `redundant_read` | experimental | — | 0 |
| **overall** | | **79.4%** | **34** |

*The evaluation **re-runs the current detectors** every time and matches stored verdicts to the findings by `finding_id`. Of 83 stored verdicts only 34 still correspond to a finding the current code emits; the other 49 are stale and **not scored** (an old rule flagged them, the new rule does not). That is why n is far smaller than in the previous table, which scored stale verdicts too.*

Read `n` before you read the precision: **`n=0` (shown as "—") means the rule
produced no findings in this round, not that it was 100% correct — the two must
never be conflated.** `redundant_read` is exactly that case: it scored 4/4 in a
round whose verdicts are not shipped, while the reproducible v3 round contains no
findings for it at all, so within this repository it is neither confirmed nor
refuted — its tier therefore stays `experimental` rather than core. For the same
reason, the human verdicts reproducible from this repository are the **145** from
the v2 and v3 rounds (204 lines in the files, 59 of them duplicates; covering 88 trajectories); earlier and later rounds were not
shipped.

**On this reproducible sample, only `termination_anomaly` (91.7%) clears the 88%
bar.** Every other rule is shipped but marked `Tier.EXPERIMENTAL`, and
`replay`/`findings` exclude experimental rules by default — an individual
experimental finding should not be read as a conclusion. Use `--tier all` when you
want volume for aggregate analysis, where the per-category discrimination is
disclosed alongside.

This is the honest result of the annotation loop, not a target that was hit. The
rules that matched their hypothesis were kept and promoted; the rest are labelled
as unproven rather than quietly tuned until the number looked good.

Both that table and the 88% bar apply **only to label-validated rules**.
`localization_failure` runs on a different channel (it compares the agent's patch
against the gold patch), and the annotation rounds shipped here carry no gold, so
it is forever `n=0` in that table. Its quality is vouched for by the measured
discrimination in the "gold channel" section below — the two are not
interchangeable.

### Notes on individual rules

- **`execution_loop`** has a sharp failure mode. Loops detected *with* an
  intervening edit were valid only **6.9% of the time (2/29)**; loops *without*
  one numbered a single case in this round (valid), far too few to support any
  claim. Gating on "no intervening edit" cut volume from **136 findings to 5**
  across the corpus, and is why this rule now fires rarely. Repeating an action
  after changing the input is usually *legitimate* debugging.
- **`localization_failure`** compares the agent's patch against the gold patch's
  file set. Without `meta["gold_files"]` it stays silent by design rather than
  guessing from heuristics. To enable it:

  ```bash
  python scripts/fetch_gold.py            # pull gold file sets into a sidecar
  python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl \
    --gold data/gold/swe-rebench-gold.jsonl
  ```

  If `huggingface.co` is unreachable (timeouts, SSL resets, shards stalling at 0
  bytes), add `--mirror https://hf-mirror.com`. The script flushes each record as
  it arrives, so a re-run skips whatever was already fetched.

  Trajectory logs do not carry the gold patch -- it exists only in the task
  dataset (SWE-rebench), so this is a required external input. Coverage is printed
  during diagnosis, because "the sidecar did not match" and "the rule genuinely
  found nothing" look identical in the output otherwise. `--gold` is accepted by
  `replay`, `diagnose`, `export` and `findings`.

  **Measured once the gold channel is connected** (297/297 `instance_id`s resolved,
  covering 300/300 trajectories — **100%**):

  | | resolved | unresolved | Δ |
  |---|---|---|---|
  | `termination_anomaly` | 5.3% (8/150) | 20.7% (31/150) | +15.3 |
  | `localization_failure` | 3.3% (5/150) | **18.0% (27/150)** | **+14.7** |

  Counted per trajectory (several findings of one category in a run count once).
  `localization_failure` is the **second-sharpest** discriminator in the table,
  behind `termination_anomaly`, and the main source of flagged waste: of the 1.6
  wasted steps per run that `diagnose` reports, 1.4 come from it. Its tier stays
  `experimental`: the 88% bar is defined over annotation precision, and this rule
  cannot be annotation-validated (the annotations contain no gold); promoting it on
  a different yardstick would empty `core` of meaning. It is currently the one rule
  backed by external ground truth.
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

The metrics that do carry signal are process-shape metrics. This table is also
emitted by `scripts/discrimination.py` (the AUC column takes *failure* as the
positive class, so a value below 0.5 means "lower is worse" and inverts to above
0.5):

| Metric | AUC (positive = failure) | Inverted | Resolved | Unresolved |
|---|---|---|---|---|
| `total_steps` | 0.694 | — | 58.83 | 71.41 |
| `source_edits` | 0.632 | — | 2.97 | 4.42 |
| `test_runs` | 0.538 | — | 14.09 | 14.86 |
| `tests_per_source_edit` | 0.386 | **0.614** | 8.16 | 6.24 |
| `test_run_ratio` | 0.402 | **0.598** | 0.2451 | 0.2192 |
| `wasted_step_ratio` | 0.520 | — | 0.0011 | 0.0046 |

Read it as: **resolved runs are shorter, edit less source, and test more per edit.**
By raw discrimination the strongest single signal is run length (`total_steps`,
0.694), but that is a symptom rather than a cause; the actionable ones are
`source_edits` and verification intensity. `wasted_step_ratio` remains
indistinguishable from chance.

"Test runs" here counts `python -c` probes as well: by decision, code the agent
writes and executes on the spot counts as checking its own work. Excluding those
probes would sharpen the tests-per-edit signal, but that is not the definition
used here.

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

### The adapters are checked against real logs

The `sweagent` adapter shipped with hand-written fixtures only, so "supports
SWE-agent" rested on the author's *reading* of the format. Feeding it the logs the
upstream project actually publishes broke that immediately:

- 1 of 22 real upstream `.traj` files could not be read **at all** -- it is
  SWE-agent's **function-calling serialization** (a `history` role stream with
  structured `tool_calls`) rather than a `trajectory` step list, which was the only
  shape the adapter knew.
- Both serializations are now supported. Re-validated: **21/21 parse, 221 steps,
  0 unmapped**; two real logs are vendored as fixtures and
  `tests/test_real_trajectories.py` re-checks them on every test run.

```bash
python scripts/fetch_sweagent_trajs.py --validate   # fetch real logs and check them
python scripts/cross_framework.py                   # both frameworks side by side
```

**On the cross-framework numbers, honestly:** the two corpora are **not
comparable**. OpenHands contributes 300 SWE-bench-style task runs; SWE-agent
contributes 21 upstream demo/smoke-test logs (plus one real SWE-bench run). So
`cross_framework.py` prints that caveat on every invocation -- it is an *adapter
validation* tool, not a framework benchmark. The only claim it currently supports
is that one pipeline reads both real formats and loses no steps in its vocabulary.

---

## Repository layout

```
trajdx/
├── trajdx/                      # the package: one log walks the whole pipeline
│   ├── __init__.py              # public surface: AgentStep / Trajectory / detect_all
│   ├── schema.py                # foundation: AgentStep, Trajectory, identity keys, patch_files()
│   ├── fingerprints.py          # 24 error kinds, exit-code extraction, fast pre-filter
│   ├── heuristics.py            # source-vs-scratch classification, test/setup detection
│   ├── metrics.py               # WSR attribution, aggregates, category lift, process shape
│   ├── report.py                # rich terminal rendering
│   ├── gold.py                  # gold-patch sidecar: makes localization_failure computable
│   ├── cli.py                   # replay / diagnose / export / findings / detectors / adapters
│   │
│   ├── adapters/                # normalisation: heterogeneous logs -> one event sequence
│   │   ├── __init__.py          # importing the package registers every adapter
│   │   ├── base.py              # adapter protocol + log-format sniffing
│   │   ├── openhands.py         # pairs each tool_call with its result by tool_call_id
│   │   └── sweagent.py          # parses the .traj action language, resolves edit via state["open_file"]
│   │
│   └── detectors/               # rules: pure Python, no model, no network
│       ├── __init__.py          # importing the package registers all 8 rules
│       ├── base.py              # Finding, Category/Phase/Severity/Tier, registry
│       ├── execution_loop.py    # repeated actions, repeated errors, A-B-A-B thrashing
│       ├── localization.py      # blind_search, redundant_read, localization_failure
│       ├── verification.py      # verification_gap, weak_verification
│       ├── termination.py       # no submit, iteration cap, patch ignoring source
│       └── environment.py       # repeatedly failing setup/install commands, timeout walls
│
├── tests/                       # test suite (includes the README drift guard)
│   ├── conftest.py              # synthetic trajectories and shared fixtures
│   ├── test_schema.py           # identity keys, patch parsing
│   ├── test_adapters.py         # conversion correctness for both frameworks
│   ├── test_detectors.py        # rule logic (the largest test file)
│   ├── test_fingerprints.py     # error classification
│   ├── test_heuristics.py       # source-vs-test file classification
│   ├── test_metrics.py          # definitions of the process-shape metrics
│   ├── test_gold.py             # gold attachment and localization_failure
│   ├── test_readme_tables.py    # README numbers must equal script output (drift guard)
│   ├── test_real_trajectories.py # adapters checked against upstream real logs
│   └── data/sweagent/           # vendored real SWE-agent logs (verbatim, MIT)
│
├── scripts/                     # offline analysis scripts, not runtime dependencies
│   ├── fetch_trajectories.py    # download and sample raw trajectories from the HF pool
│   ├── fetch_gold.py            # fetch gold patch file sets (mirror-aware, resumable)
│   ├── fetch_sweagent_trajs.py  # fetch real SWE-agent .traj logs for cross-framework checks
│   ├── cross_framework.py       # two frameworks side by side (prints the incomparability caveat)
│   ├── detector_profile.py      # per-detector volume and step coverage
│   ├── discrimination.py        # AUC of each metric against the resolved label
│   ├── evaluate.py              # precision, threshold curve, the README table
│   └── check_regression.py      # non-zero exit if a core rule drops below 88%
│
├── data/
│   ├── raw/                     # gitignored: raw trajectories (~81 MB / 300 runs),
│   │                            # regenerate with fetch_trajectories.py
│   ├── labels/                  # tracked: LLM pre-labels + human review verdicts
│   ├── gold/                    # gitignored: gold patch file sets (with a gap list)
│   └── reports/                 # gitignored: aggregate summaries and stats.csv
│
├── docs/
│   └── annotation_guide.md      # the labelling rubric shared by all three rounds
│
├── README.md                    # Chinese documentation
├── README.en.md                 # English documentation
├── pyproject.toml               # dependencies and the `trajdx` console entry point
└── LICENSE                      # MIT
```

The data split is deliberate: `data/raw/` and `data/reports/` are large and
reproducible from a script, so they stay out of the repository, while
`data/labels/` holds **irreproducible human judgements** and must be tracked.
Re-running `scripts/evaluate.py` needs only the two files under `data/labels/`;
it does not depend on the raw trajectories.

## Re-running the evaluation

```bash
# full evaluation: per-detector precision, confidence bands, threshold curve
python scripts/evaluate.py \
  --labels "data/labels/labelled_v3_*.jsonl"

# just the table the README quotes
python scripts/evaluate.py \
  --labels "data/labels/labelled_v3_*.jsonl" --markdown

# bar check: exits non-zero if any core rule drops below 88%, or has no sample
python scripts/check_regression.py

# gold channel: the discrimination of localization_failure (needs fetch_gold.py)
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl \
  --gold data/gold/swe-rebench-gold.jsonl
```

Label files are read with `utf-8-sig` because the annotation pass writes a BOM.

The last command depends on `data/raw/` and `data/gold/`, both gitignored, so it
cannot run in a bare clone; the matching check in `tests/test_gold.py` skips when
the data is absent rather than pretending to pass.

`scripts/check_regression.py` is invoked by the test suite as well, so the core
tier is not a promise in prose but an assertion that breaks the build: **a rule
that falls below the bar must be fixed or demoted, never just re-described.**
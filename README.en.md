# trajdx

**Autopsy for failed code-agent runs: which step went wrong, and why.**

Language: [中文](README.md) · [English](README.en.md)

## What it is, in one paragraph

SWE-bench-style evaluation gives an agent one bit per task — resolved or not. When it fails, nothing tells you *which step* went off the rails. `trajdx` fills that gap: it reads raw agent logs and uses pure-Python rules to pinpoint concrete problems — "repeated the same command across steps 34–50", "never verified its own edits" — with the exact step ranges.

- **No LLM** in the detection path. No network. No Docker.
- ~3 ms per trajectory.
- Reads OpenHands and SWE-agent logs.

Current status: **all tests pass (295), the pipeline works; the v4 review round covers 128 findings on 700 trajectories at 92.2% overall precision — `termination_anomaly` (core) 98.8%, `edit_error` / `redundant_read` / `verification_gap` fully valid, `execution_loop` dropped to 30.8% after the sample expansion (defect located, see below), and `blind_search` produced no hits. Never read a single experimental finding as a conclusion.**

## Quickstart

```bash
pip install -e ".[dev]"
pytest -q                                   # 295 tests

python -m trajdx.cli detectors              # what rules exist
python -m trajdx.cli adapters               # what log formats are supported
python -m trajdx.cli replay data/raw/openhands_sample.jsonl --index 0
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl
python -m trajdx.cli export data/raw/openhands_sample.jsonl --out data/reports/diag.jsonl
python -m trajdx.cli findings data/raw/openhands_sample.jsonl --out data/labels/to_label.jsonl
```

> `data/raw/` is gitignored (~81 MB) and absent from a bare clone; run
> `python scripts/fetch_trajectories.py` first, or commands that touch raw
> trajectories — and some tests — will be skipped.

## How it works

| Stage | Module | Output |
|---|---|---|
| Normalise | `trajdx.adapters` | `Trajectory` of `AgentStep` |
| Fingerprint errors | `trajdx.fingerprints` | 24 error kinds + exit codes |
| Detect | `trajdx.detectors` | `Finding` objects with step ranges and evidence |
| Quantify waste | `trajdx.metrics` | Wasted Step Ratio, per-category attribution |
| Report | `trajdx.report`, `trajdx.cli` | replay / diagnose / export / findings |

A trajectory becomes a sequence of steps; each step is a decision–action–observation triple carrying three identity keys:

- `action_key` — what the agent *meant* to do. Volatile tokens (paths, ids, line numbers) are scrubbed, so `pytest tests/test_x.py` and `pytest tests/test_y.py` collapse together.
- `exact_key` — the literal action including its payload. Two edits to the same file are only "the same" if the replacement text matches too.
- `observation_key` — a hash of the *result*.

Why the split? Because **repeating an action is not automatically a failure**: repeating it after changing the input is usually legitimate debugging. The gap between `action_key` and `exact_key` is how loop detection tells the two apart — that is what keeps the rules from crying wolf.

## The rules: which ones can you trust?

| Rule | What it detects | Reliability today (v4 review round, 700 trajectories) |
|---|---|---|
| `termination_anomaly` | bad endings: no submit, iteration cap, patch ignoring source | **core, 79/80 (98.8%)**, and the sharpest resolved/unresolved signal |
| `edit_error` | consecutive edit-tool rejections (the agent fighting its editor) | **12/12 valid** |
| `redundant_read` | reading the same content repeatedly | **6/6 valid** |
| `verification_gap` | no verification after the final change | **16/16 valid** (v4 fixed the "creating a verification script counted as a source edit" defect) |
| `execution_loop` | repeated actions, repeated errors, discard-and-reapply cycles | **4/13 (30.8%)**: the expanded sample exposed that the exact/error/revert_cycle sub-patterns all mistake "re-running after a fix" and "A/B baseline checkpoints" for loops — the next defect to fix |
| `localization_failure` | edited the wrong files (vs. gold patch) | backed by external ground truth (second-best discriminator), not annotation-validatable, stays experimental |
| `blind_search` | blind search | zero hits across 700 trajectories since the convergence condition was added — it no longer cries wolf, but it has no samples yet either |
| `weak_verification` | many edits, little testing | first-ever hit in the 700-trajectory sample, judged 1/1 valid; still a population-level signal, individual precision awaits more samples |
| `environment_stuck` | repeatedly failing setup/install commands | **dormant on this corpus**: 2 hits across 4,096 real runs (0.05%); this failure mode barely exists in pre-built containers |

`replay` / `findings` exclude experimental rules by default; use `--tier all` when you want volume for aggregate analysis.

The two tables below are the evidence behind that "reliability today" column. All of them reproduce from scripts.

### Table 1: v4-round independent AI review precision (128 findings, 700 trajectories)

Generated by the command shown, not hand-written; the test suite checks both READMEs against the script output verbatim. **Read `n` before the precision**: `n=0` (shown as "—") means the rule produced no findings in this round — not that it was 100% correct; the two must never be conflated.

```bash
python scripts/evaluate.py --raw data/raw/openhands_sample_v4.jsonl \
  --labels data/labels/reviewed_ai_identity_v4.jsonl --markdown
```

| Detector | Tier | Precision | n |
|---|---|---|---|
| `edit_error` | experimental | 100.0% | 12 |
| `redundant_read` | experimental | 100.0% | 6 |
| `verification_gap` | experimental | 100.0% | 16 |
| `weak_verification` | experimental | 100.0% | 1 |
| `termination_anomaly` | core | 98.8% | 80 |
| `execution_loop` | experimental | 30.8% | 13 |
| `blind_search` | experimental | — | 0 |
| `environment_stuck` | experimental | — | 0 |
| `localization_failure` | experimental | — | 0 |
| **overall** | | **92.2%** | **128** |

**Review protocol**: `data/labels/reviewed_ai_identity_v4.jsonl` covers all 128 findings the current code emits on 700 trajectories (the original 300-trajectory evaluation sample plus 400 more drawn outcome-stratified from the local 4,096-run pool by `scripts/expand_sample.py`, disjoint from the original sample). Review packets were outcome-blinded, and every row carries the run/evidence signature plus code and raw-data hashes. **This is an AI review, not human ground truth, and not an independent held-out set: every row is `reviewer_type=ai` and `human_verified=false`.** Of the 128 verdicts, 70 are freshly judged this round and 58 are carried over at identical signature — adoption is allowed only when the rule did not change and the finding is byte-identical (same `run_id`, detector and `finding_signature`), and every adopted row is marked `adopted_verdict=true` for auditability. Any rule change changes signatures and voids the carry-over.

The strict gate (n>=20, core coverage>=80%, point estimate>=88%) **passes** on this review round: `termination_anomaly`, 80 findings at 98.8% with 100% coverage.

This is the honest result of the review loop, not a target that was hit: `execution_loop` collapsed to 30.8% after the sample expansion and is recorded as such rather than hidden; rules that matched their hypothesis were kept, the rest are labelled unproven rather than quietly tuned until the number looked good.

**Known defects located by this round's verdicts (next round's fixes)**:
- `execution_loop/exact` calls "re-running the repro script after a fix", "file navigation with different view ranges" and "state checkpoints" loops (9 false positives, one family: a repeated command with substantive state changes in between is not a loop);
- `execution_loop/error` counts repeated DeprecationWarnings as failures and treats "substantive investigation between failures" as spinning;
- `execution_loop/revert_cycle` reproduces the stash false-positive family: `git stash → baseline check → git stash pop` is diagnosed as "edited then reverted" — the rule cannot see the future pop;
- `termination_anomaly/patch_ignores_source` misclassifies the real source file `pre_commit_hooks/check_yaml.py` as scratch (the `check[_-]` prefix, same family as the `checker.py` bug fixed in v3).

The 3 v3 `verification_gap` false positives (creating verification scripts of any name — `edge_cases.py` and friends — counted as source edits) **are fixed this round**: verification scripts are now recognized by behaviour (created and run within this trajectory) rather than by name, with 3 regression tests from the real rejected verdicts. The fix also voids one v3 *valid* verdict (dask-6564) — that verdict itself misread the creation of a diagnostic script as a source edit, which does not stand under the corrected, consistent definition.

**After fixing rules the review must be redone, never carried over — which is exactly what this round did.**

### Table 2: the gold channel — `localization_failure` discrimination

`localization_failure` does not run on the annotation channel: it compares the agent's patch against the file set touched by the gold patch. Trajectory logs do not carry the gold patch — it exists only in the task dataset (SWE-rebench):

```bash
python scripts/fetch_gold.py            # pull gold file sets into a sidecar
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl \
  --gold data/gold/swe-rebench-gold.jsonl
```

If `huggingface.co` is unreachable (timeouts, SSL resets, shards stalling at 0 bytes), add `--mirror https://hf-mirror.com`. The script flushes each record as it arrives, so a re-run skips whatever was already fetched. Coverage is printed during diagnosis, because "the sidecar did not match" and "the rule genuinely found nothing" look identical otherwise. `--gold` is accepted by `replay`, `diagnose`, `export` and `findings`.

Measured once the gold channel was connected (297/297 `instance_id`s resolved, covering 300/300 trajectories — **100%**):

| | resolved | unresolved | Δ |
|---|---|---|---|
| `termination_anomaly` | 5.3% (8/150) | 20.7% (31/150) | +15.3 |
| `localization_failure` | 3.3% (5/150) | **18.0% (27/150)** | **+14.7** |

Counted per trajectory (several findings of one category in a run count once). `localization_failure` is the second-sharpest discriminator in the table, behind `termination_anomaly`, and the main source of flagged waste: of the 1.44 wasted steps per run that `diagnose` reports, 1.36 come from it. Its tier stays experimental: the 88% bar is defined over annotation precision, and this rule cannot be annotation-validated (the annotations contain no gold) — promoting it on a different yardstick would empty `core` of meaning. It is currently the one rule backed by external ground truth.

## Wasted Step Ratio: a metric that did not pan out

`WSR` charges every flagged step to exactly one waste category by severity, so `WSR <= 1.0` always holds and no step is double-counted.

```bash
python scripts/detector_profile.py     # per-detector volume and step coverage
python scripts/discrimination.py       # AUC of each metric against the resolved label
```

**Straight talk: WSR does not predict failure.** Its Mann-Whitney AUC against the resolved/unresolved label is **0.520** — indistinguishable from chance. Reporting it as a failure predictor would be a mistake, so the docs and CLI describe it strictly as an *efficiency* metric: it answers "how much of this run was spent re-treading ground", not "was this run going to succeed".

The metrics that do carry signal are process-shape metrics. This table is emitted by `scripts/discrimination.py` (the AUC column takes *failure* as the positive class, so a value below 0.5 means "lower is worse" and inverts to above 0.5):

| Metric | AUC (positive = failure) | Inverted | Resolved | Unresolved |
|---|---|---|---|---|
| `total_steps` | 0.694 | — | 58.83 | 71.41 |
| `source_edits` | 0.637 | — | 2.99 | 4.51 |
| `test_runs` | 0.538 | — | 14.09 | 14.86 |
| `tests_per_source_edit` | 0.382 | **0.618** | 8.16 | 6.17 |
| `test_run_ratio` | 0.402 | **0.598** | 0.2451 | 0.2192 |
| `novel_observation_ratio` | 0.430 | **0.570** | 0.9269 | 0.9174 |
| `wasted_step_ratio` | 0.520 | — | 0.0008 | 0.0017 |

One-sentence reading: **resolved runs are shorter, edit less source, and test more per edit.** The strongest raw signal is run length (`total_steps`, 0.694), but that is a symptom rather than a cause; the actionable ones are `source_edits` and verification intensity.

`novel_observation_ratio`, added this round, is the share of steps that produced a never-before-seen observation, computed straight from the `observation_key` hashes with **no detector in the loop**. It beats WSR (0.520) but stays below verification intensity — honest record: observation novelty carries signal, but it is not decisive.

Definition note: "test runs" here counts `python -c` probes as well — code the agent writes and executes on the spot counts as checking its own work. Excluding those probes would sharpen the tests-per-edit signal, but that is not the definition used here.

## Data and adapters

`data/raw/openhands_sample.jsonl` holds 300 trajectories sampled from a pool of 67,074 (150 resolved / 150 unresolved, 3 duplicate `instance_id`s left in place rather than silently deduplicated). For the v4 round, 400 more trajectories were drawn outcome-stratified from the local 4,096-run pool by `scripts/expand_sample.py` (200/200, disjoint from the original sample, reproducible via `--seed`) and merged into `data/raw/openhands_sample_v4.jsonl` (700 trajectories) for the review round. The process-shape and AUC tables still use the original 300-trajectory sample so the numbers stay comparable with earlier rounds.

Corpus facts worth knowing before writing a rule:

- `exit_status` is `submit` for 263 runs and `RuntimeError: Agent reached maximum iteration (100)` for 37.
- Mean edits per trajectory is **11.04**, but mean *source* edits is **3.70** — **67% of all edits target scratch or test files**. Any rule that counts "edits" without filtering will be dominated by noise.
- Tool-call mix: `execute_bash` 9865, `str_replace_editor` 8343, `think` 801, `task_tracker` 264, `finish` 263.
- Assumptions *not* present in this data: no assistant message ever issues multiple tool calls at once, and `model_patch` is never empty.

Adapters: `openhands` (pairs a `tool_call` with its `tool` result by `tool_call_id`) and `sweagent` (resolves `edit` targets against `state["open_file"]`). Both classify an error only when the step kind can actually carry one, so error fingerprints are never scraped out of file contents.

### The adapters are checked against real logs

The `sweagent` adapter shipped with hand-written fixtures only, so "supports SWE-agent" rested on the author's *reading* of the format. Feeding it the logs the upstream project actually publishes broke that immediately: 1 of 22 real upstream `.traj` files could not be read **at all** — it is SWE-agent's function-calling serialization (a `history` role stream with structured `tool_calls`) rather than the `trajectory` step list the adapter knew.

Both serializations are now supported. Re-validated: **21/21 parse, 221 steps, 0 unmapped**; two real logs are vendored as fixtures and `tests/test_real_trajectories.py` re-checks them on every test run.

```bash
python scripts/fetch_sweagent_trajs.py --validate   # fetch real logs and check them
python scripts/cross_framework.py                   # both frameworks side by side
```

**On the cross-framework numbers, honestly:** the two corpora are **not comparable** — OpenHands contributes 300 SWE-bench-style task runs; SWE-agent contributes 21 upstream demo/smoke-test logs (plus one real SWE-bench run). `cross_framework.py` prints that caveat on every invocation: it is an *adapter validation* tool, not a framework benchmark. The only claim it currently supports is that one pipeline reads both real formats and loses no steps in its vocabulary.

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
│       ├── __init__.py          # importing the package registers all 9 rules
│       ├── base.py              # Finding, Category/Phase/Severity/Tier, registry
│       ├── execution_loop.py    # repeated actions, repeated errors, A-B-A-B thrash, revert cycles
│       ├── edit_error.py        # consecutive edit-tool rejections
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
│   ├── test_identity_loading.py # iter_file / limit and versioned-label loading
│   ├── test_pool_validation.py  # bounded stratified sampling validation
│   ├── test_reviewed_labels.py  # consistency checks on AI-reviewed labels
│   ├── test_readme_tables.py    # README numbers must equal script output (drift guard)
│   ├── test_real_trajectories.py # adapters checked against upstream real logs
│   └── data/sweagent/           # vendored real SWE-agent logs (verbatim, MIT)
│
├── scripts/                     # offline analysis scripts, not runtime dependencies
│   ├── fetch_trajectories.py    # download and sample the 300 raw trajectories
│   ├── fetch_openhands_pool.py  # fetch the published 67k OpenHands pool on demand
│   ├── fetch_sweagent_pool.py   # fetch the 80k SWE-agent pool and convert to JSONL
│   ├── fetch_gold.py            # fetch gold patch file sets (mirror-aware, resumable)
│   ├── fetch_sweagent_trajs.py  # fetch real SWE-agent .traj logs for cross-framework checks
│   ├── cross_framework.py       # two frameworks side by side (prints the incomparability caveat)
│   ├── detector_profile.py      # per-detector volume and step coverage
│   ├── discrimination.py        # AUC of each metric against the resolved label
│   ├── evaluate.py              # precision, threshold curve, the README table
│   ├── check_regression.py      # non-zero exit if a core rule drops below 88%
│   ├── expand_sample.py         # draw a stratified expansion sample disjoint from existing samples
│   ├── validate_pool.py         # bounded stratified parsing validation (no network, no precision claims)
│   ├── prepare_review.py        # build outcome-blinded evidence packets for review
│   ├── record_termination_review.py  # persist termination-related review verdicts
│   ├── record_verification_review.py # persist verification-related review verdicts
│   └── finalize_review.py       # assemble review verdicts (--adopt carries same-signature verdicts) into versioned labels
│
├── data/
│   ├── raw/                     # gitignored: raw trajectories (300-run sample + 4,096-run pool),
│   │                            # regenerate with fetch_trajectories.py / fetch_openhands_pool.py
│   ├── labels/                  # tracked: LLM pre-labels + human review verdicts + AI review verdicts
│   ├── gold/                    # gitignored: gold patch file sets (with a gap list)
│   └── reports/                 # gitignored: aggregate summaries and stats.csv
│
├── docs/
│   ├── annotation_guide.md      # the labelling rubric shared by all three rounds
│   └── bugfix_validation.md     # record of the correctness fixes and bounded validation
│
├── README.md                    # Chinese documentation
├── README.en.md                 # English documentation
├── pyproject.toml               # dependencies and the `trajdx` console entry point
└── LICENSE                      # MIT
```

The data split is deliberate: `data/raw/`, `data/gold/` and `data/reports/` are large and reproducible from scripts, so they stay out of the repository, while `data/labels/` holds **irreproducible review judgements** and must be tracked. Evaluation requires raw trajectories. Existing v2/v3 labels are available only for explicit `--allow-legacy` historical comparison; the current evaluation uses `data/labels/reviewed_ai_identity_v4.jsonl` (v4 independent AI review, 700 trajectories).

## Re-running the evaluation

```bash
# current reviewed labels: strict gate + full evaluation (700-trajectory merged sample)
# evaluate.py re-runs the current detectors every time; stored labels are only
# matched against what the code emits today, never scored directly
python scripts/check_regression.py --raw data/raw/openhands_sample_v4.jsonl \
  --labels data/labels/reviewed_ai_identity_v4.jsonl
python scripts/evaluate.py --raw data/raw/openhands_sample_v4.jsonl \
  --labels data/labels/reviewed_ai_identity_v4.jsonl

# draw an expansion sample from the pool, disjoint from existing samples
python scripts/expand_sample.py --n 400 --seed 20261001 \
  --exclude data/raw/openhands_sample.jsonl

# historical comparison only (not a proof of current quality)
python scripts/evaluate.py --labels "data/labels/labelled_v3_*.jsonl" --allow-legacy

# gold channel: the discrimination of localization_failure (needs fetch_gold.py)
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl \
  --gold data/gold/swe-rebench-gold.jsonl

# bounded parsing validation: first 1200 records, stratified sample, one record at a time
python scripts/validate_pool.py --input data/raw/openhands_pool.jsonl \
  --sample-out data/raw/validation_openhands.jsonl \
  --report data/reports/validation_openhands_after.json
```

Label files are read with `utf-8-sig` because the annotation pass writes a BOM.

The gold command depends on gitignored `data/raw/` and `data/gold/`, so it cannot run in a bare clone; the matching check in `tests/test_gold.py` skips when the data is absent rather than pretending to pass.

The strict gate currently **passes** against `reviewed_ai_identity_v4.jsonl`; the test suite also asserts that it rejects unversioned legacy labels. **Verdicts must never be auto-migrated just to pass the gate: whenever the evidence changes (a rule was fixed, a finding changed identity), the review must be redone.** The one exception is `finalize_review.py --adopt`: it carries over prior verdicts only when `run_id`, detector and `finding_signature` all match (the finding is byte-identical), marking each adopted row `adopted_verdict=true`. New verdicts must retain `finding_id`, `run_id`, and `finding_signature`.

JSONL loading APIs support `iter_file` and a limit enforced before reading; JSON array files still load in full. The `validate_pool.py` sample does not represent the full corpus, produces no precision/recall, and ranks no frameworks.

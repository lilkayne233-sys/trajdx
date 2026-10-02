# trajdx

**Autopsy for failed code-agent runs: which step went wrong, and why.**

Language: [中文](README.md) · [English](README.en.md)

## What it is, in one paragraph

SWE-bench-style evaluation gives an agent one bit per task — resolved or not. When it fails, nothing tells you *which step* went off the rails. `trajdx` fills that gap: it reads raw agent logs and uses pure-Python rules to pinpoint concrete problems — "repeated the same command across steps 34–50", "never verified its own edits" — with the exact step ranges.

- **No LLM** in the detection path. No network. No Docker.
- ~3 ms per trajectory.
- Reads OpenHands and SWE-agent logs.

Current status: **all tests pass (307), the pipeline works; the v6 expansion round covers 230 findings on 1,400 OpenHands trajectories at 95.2% overall precision (122 verdicts adopted at identical signature from v5, 108 freshly judged), plus a cross-framework check on 188 SWE-agent (llama-8B) trajectories (347 findings, 84.4% overall). `termination_anomaly` (core) 148/155, `edit_error` fully valid, and `blind_search` collected its first 8 hits (6 valid). Never read a single experimental finding as a conclusion.**

## Quickstart

```bash
pip install -e ".[dev]"
pytest -q                                   # 307 tests

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

| Rule | What it detects | Reliability today (v5 review round, 700 trajectories) |
|---|---|---|
| `termination_anomaly` | bad endings: no submit, iteration cap, patch ignoring source | **core, 81/81 valid** (the `check_yaml.py` scratch misclassification is fixed), and the sharpest resolved/unresolved signal |
| `edit_error` | consecutive edit-tool rejections (the agent fighting its editor) | **12/12 valid** |
| `redundant_read` | reading the same content repeatedly | **6/6 valid** |
| `verification_gap` | no verification after the final change | **16/16 valid** (v4 fixed the "creating a verification script counted as a source edit" defect) |
| `execution_loop` | repeated actions, repeated errors, discard-and-reapply cycles | **5/6 (83.3%)**: v5 fixed the family that mistook "re-running after a fix", "A/B baseline checkpoints" and "stash/pop diagnosis" for loops; one grey-zone residual remains (see below) |
| `localization_failure` | edited the wrong files (vs. gold patch) | backed by external ground truth (second-best discriminator), not annotation-validatable, stays experimental |
| `blind_search` | blind search | zero hits on the OpenHands main sample; the SWE-agent cross-check (weaker-model trajectories) supplied the first 8 hits, 6 of them valid — the rule works but is rare in strong-model corpora |
| `weak_verification` | many edits, little testing | 2/3 on the OpenHands main sample; 0/3 on the SWE-agent cross-check (running the CLI tool's own command counts as verification there, which the rule over-flags). Criteria still maturing |
| `environment_stuck` | repeatedly failing setup/install commands | **dormant on this corpus**: 2 hits across 4,096 real runs (0.05%); this failure mode barely exists in pre-built containers |

`replay` / `findings` exclude experimental rules by default; use `--tier all` when you want volume for aggregate analysis.

The two tables below are the evidence behind that "reliability today" column. All of them reproduce from scripts.

### Table 1: v6-round independent AI review precision (230 findings, 1,400 OpenHands trajectories)

Generated by the command shown, not hand-written; the test suite checks both READMEs against the script output verbatim. **Read `n` before the precision**: `n=0` (shown as "—") means the rule produced no findings in this round — not that it was 100% correct; the two must never be conflated.

```bash
python scripts/evaluate.py --raw data/raw/openhands_sample_v6.jsonl \
  --labels data/labels/reviewed_ai_identity_v6.jsonl --markdown
```

| Detector | Tier | Precision | n |
|---|---|---|---|
| `edit_error` | experimental | 100.0% | 21 |
| `redundant_read` | experimental | 100.0% | 14 |
| `verification_gap` | experimental | 96.2% | 26 |
| `termination_anomaly` | core | 95.5% | 155 |
| `execution_loop` | experimental | 81.8% | 11 |
| `weak_verification` | experimental | 66.7% | 3 |
| `blind_search` | experimental | — | 0 |
| `environment_stuck` | experimental | — | 0 |
| `localization_failure` | experimental | — | 0 |
| **overall** | | **95.2%** | **230** |

**Review protocol**: `data/labels/reviewed_ai_identity_v6.jsonl` covers all 230 findings the current code emits on 1,400 trajectories (the 700 from the v4 round plus 700 more drawn outcome-stratified from the local 4,096-run pool by `scripts/expand_sample.py --seed 20261002`, disjoint from the existing sample). Review packets were outcome-blinded, and every row carries the run/evidence signature plus code and raw-data hashes. **This is an AI review, not human ground truth, and not an independent held-out set: every row is `reviewer_type=ai` and `human_verified=false`.** Of the 230 verdicts, 122 are carried over at identical signature from v5 (adoption is allowed only when the rule did not change and the finding is byte-identical; every adopted row is marked `adopted_verdict=true` for auditability) and 108 are freshly judged this round (74 of them `termination_anomaly`).

What the expansion bought: the new data surfaced invalids invisible in the small v5 sample. The 7 `termination_anomaly/iteration_cap` false positives share one shape — **the core fix was already complete and verified, and the run was killed during wrap-up** — being truncated at the step budget is not the same as having one's effort wasted. `weak_verification` saw its first invalid: the rule counted throwaway SQL reproduction fixtures as edits, exaggerating the "many edits, little testing" contrast.

The strict gate (n>=20, core coverage>=80%, point estimate>=88%) **passes** on this review round: `termination_anomaly`, 155 findings at 95.5% with 100% coverage.

This is the honest result of the review loop, not a target that was hit: rules that matched their hypothesis were kept, the rest are labelled unproven rather than quietly tuned until the number looked good.

**What v4 → v5 fixed (the 9 v4 false positives, one family each)**:
- the `execution_loop` intervention gate widened from "nothing was edited" to "nothing changed state": re-running a probe after `pip install`, `rm`, rebuilding a fixture directory or a `git stash` baseline swap re-samples a different world and is hypothesis testing, not a loop (3 exact false positives gone);
- `execution_loop/error` no longer counts repeated `DeprecationWarning`-class warnings as failures (1 gone) and is covered by the same gate (1 stash A/B false positive gone);
- `execution_loop/exact` includes `view_range` in a READ's exact key: navigating one file through different ranges is navigation, not repetition (2 gone);
- `execution_loop/revert_cycle` stack-pairs stashes: `stash → baseline check → pop` never discarded anything and is not "edited then reverted"; an unrestored stash (run killed while stashed) still is (1 gone);
- `termination_anomaly/patch_ignores_source` no longer lets the scratch name override observed behaviour: a pre-existing file the agent successfully modified is real source, so `pre_commit_hooks/check_yaml.py` is no longer misclassified by the `check[_-]` prefix (1 gone).

**An intentionally kept grey zone**: `makhidkarun__traveller_pyroute-58` — the same pytest assertion error three times with grep/read investigation in between. It is structurally identical to the error loops the v3 round judged *valid* (only reads between failures), and no clean syntactic criterion keeps one while rejecting the other. The rule currently keeps the finding; its v4 invalid verdict stands and counts against precision, as recorded.

**A new lesson recorded during the fix**: `LSSTDESC__gcr-catalogs-419` asks for new catalog config files — the agent's freshly created YAMLs *are* the correct deliverable, so "a file the agent created is tooling" holds only for **script extensions**. The boundary is written into the `patch_ignores_source` logic and its tests.

**After fixing rules the review must be redone, never carried over — carry-over here is limited to byte-identical findings.**

### Table 1b: cross-framework check — precision on the SWE-agent (llama-8B) corpus

Do the same rules transfer to another agent framework? From the local SWE-agent pool (nebius/SWE-agent-trajectories; 6,670 runs but only 307 distinct tasks) 188 trajectories were drawn — **at most one run per task**, so repeated runs of one task cannot cluster the sample; the outcome field is `target` rather than `resolved`, normalized by the existing sweagent adapter. The review protocol is identical to the main sample (outcome-blinded, independent AI, all 350 findings freshly judged, no adoption).

```bash
python scripts/evaluate.py --raw data/raw/sweagent_sample_v1.jsonl \
  --labels data/labels/reviewed_ai_identity_v6_sweagent.jsonl --markdown
```

| Detector | Tier | Precision | n |
|---|---|---|---|
| `edit_error` | experimental | 100.0% | 59 |
| `redundant_read` | experimental | 92.3% | 13 |
| `execution_loop` | experimental | 89.6% | 96 |
| `verification_gap` | experimental | 77.6% | 67 |
| `termination_anomaly` | core | 77.2% | 101 |
| `blind_search` | experimental | 75.0% | 8 |
| `weak_verification` | experimental | 0.0% | 3 |
| `environment_stuck` | experimental | — | 0 |
| `localization_failure` | experimental | — | 0 |
| **overall** | | **84.4%** | **347** |

(3 `uncertain` verdicts are excluded from precision.)

**How to read this table**: it does not say "the rules fail on SWE-agent" — it is empirical evidence that **precision tracks agent capability**. In weaker-model trajectories, "a failed-edit streak inside otherwise progressing work", "the fix was delivered but the run was cut off mid-wrap-up", and "running the (modified) CLI tool counts as verification" all become common, and the per-finding review honestly marks them invalid. Seven of the `verification_gap` invalids share one shape: the edit targets the CLI tool itself and the agent re-runs the tool after every edit — by precedent that is real verification, but the current rule does not recognize "running the edited tool" as a verification form (a recorded gap to fix, not a data error). Conclusion: **the cross-framework ordering of rules holds (edit_error most robust; termination_anomaly drops below the 88% line), but experimental-layer absolute numbers must be read as corpus-bound**. The SWE-agent numbers describe this one 8B model's runs, not the SWE-agent framework itself.


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

**Straight talk: WSR does not predict failure.** Its Mann-Whitney AUC against the resolved/unresolved label on the OpenHands main sample is **0.518** — indistinguishable from chance. Reporting it as a failure predictor would be a mistake, so the docs and CLI describe it strictly as an *efficiency* metric: it answers "how much of this run was spent re-treading ground", not "was this run going to succeed".

The metrics that do carry signal are process-shape metrics. This table is emitted by `scripts/discrimination.py` (sample: the 1,400 OpenHands main trajectories; the AUC column takes *failure* as the positive class, so a value below 0.5 means "lower is worse" and inverts to above 0.5):

| Metric | AUC (positive = failure) | Inverted | Resolved | Unresolved |
|---|---|---|---|---|
| `total_steps` | 0.664 | — | 59.64 | 70.27 |
| `source_edits` | 0.618 | — | 2.96 | 4.32 |
| `test_runs` | 0.545 | — | 14.16 | 15.02 |
| `tests_per_source_edit` | 0.399 | **0.601** | 8.11 | 6.58 |
| `test_run_ratio` | 0.426 | **0.574** | 0.2426 | 0.2227 |
| `novel_observation_ratio` | 0.462 | **0.538** | 0.9257 | 0.9194 |
| `wasted_step_ratio` | 0.518 | — | 0.0008 | 0.0042 |

One-sentence reading: **resolved runs are shorter, edit less source, and test more per edit.** The strongest raw signal is run length (`total_steps`, 0.664), but that is a symptom rather than a cause; the actionable ones are `source_edits` and verification intensity.

`novel_observation_ratio`, added this round, is the share of steps that produced a never-before-seen observation, computed straight from the `observation_key` hashes with **no detector in the loop**. It beats WSR (0.518) but stays below verification intensity — honest record: observation novelty carries signal, but it is not decisive.

**A surprise from the cross-framework check**: on the SWE-agent (llama-8B) corpus WSR's AUC jumps to **0.687** (resolved 0.0119 / unresolved 0.1878, `--framework sweagent`), near the "worth putting on a slide" 0.7 line. Explanation: a strong model keeps producing new information even while spinning, flattening WSR; a weak model's spinning is genuine treading-in-place, which WSR captures faithfully. **"WSR is a weak metric" was a strong-model-corpus conclusion, not a universal one** — exactly what cross-framework checks are for.

Definition note: "test runs" here counts `python -c` probes as well — code the agent writes and executes on the spot counts as checking its own work. Excluding those probes would sharpen the tests-per-edit signal, but that is not the definition used here.

## Data and adapters

`data/raw/openhands_sample.jsonl` holds 300 trajectories sampled from a pool of 67,074 (150 resolved / 150 unresolved, 3 duplicate `instance_id`s left in place rather than silently deduplicated). The v4 round added 400, and the v6 round drew 700 more outcome-stratified from the local 4,096-run pool (`scripts/expand_sample.py --n 700 --seed 20261002`, disjoint from the existing sample), merged into `data/raw/openhands_sample_v6.jsonl` (1,400 trajectories) for the review round. The cross-framework sample `data/raw/sweagent_sample_v1.jsonl` (188 trajectories) was drawn from the 6,670-run SWE-agent pool stratified by `target` with one run per task (`--outcome-key target --max-per-instance 1`; only 38 tasks have a resolved first run, so the quota was not force-filled).

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

The data split is deliberate: `data/raw/`, `data/gold/` and `data/reports/` are large and reproducible from scripts, so they stay out of the repository, while `data/labels/` holds **irreproducible review judgements** and must be tracked. Evaluation requires raw trajectories. Existing v2/v3 labels are available only for explicit `--allow-legacy` historical comparison; the current evaluation uses `data/labels/reviewed_ai_identity_v6.jsonl` (v6 independent AI review, 1,400 OpenHands trajectories) and `data/labels/reviewed_ai_identity_v6_sweagent.jsonl` (cross-framework check, 188 SWE-agent trajectories).

## Re-running the evaluation

```bash
# current reviewed labels: strict gate + full evaluation (700-trajectory merged sample)
# evaluate.py re-runs the current detectors every time; stored labels are only
# matched against what the code emits today, never scored directly
python scripts/check_regression.py --raw data/raw/openhands_sample_v6.jsonl \
  --labels data/labels/reviewed_ai_identity_v6.jsonl
python scripts/evaluate.py --raw data/raw/openhands_sample_v6.jsonl \
  --labels data/labels/reviewed_ai_identity_v6.jsonl

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

The strict gate currently **passes** against `reviewed_ai_identity_v6.jsonl`; the test suite also asserts that it rejects unversioned legacy labels. **Verdicts must never be auto-migrated just to pass the gate: whenever the evidence changes (a rule was fixed, a finding changed identity), the review must be redone.** The one exception is `finalize_review.py --adopt`: it carries over prior verdicts only when `run_id`, detector and `finding_signature` all match (the finding is byte-identical), marking each adopted row `adopted_verdict=true`. New verdicts must retain `finding_id`, `run_id`, and `finding_signature`.

JSONL loading APIs support `iter_file` and a limit enforced before reading; JSON array files still load in full. The `validate_pool.py` sample does not represent the full corpus, produces no precision/recall, and ranks no frameworks.

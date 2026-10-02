# Recall audit: what the tool missed on failed runs

Every precision number in the README answers "how many alarms were true".
This audit answers the other half: **of the failed runs the tool stayed
silent on, how many contained a process signal a rule could have caught?**

## Method

Population: the 1,400-trajectory v6 main sample (OpenHands, Qwen3-Coder-480B).
Of the 700 failed (unresolved) runs, **560 (80%) produced zero findings** --
the tool said nothing at all. A fixed-seed sample of 60 of those 560 was drawn
(`scripts/select_recall_sample.py --seed 20261003`, sample at
`data/raw/recall_sample.jsonl`) and each trajectory was read end-to-end by an
AI analyst with no knowledge of this audit's hypotheses. The verdict is an
estimate on n=60, not a precision claim, and the analyst is an AI, not a human.

For each trajectory the analyst named the failure cause and classified whether
a process signal existed that the current 4-rule set (or the deleted rule set)
should have caught:

| class | n | share |
|---|---|---|
| `none` — no process signal: task too hard, wrong approach from step one, or semantic mismatch with hidden tests despite a clean process | 28 | 47% |
| `new_shape` — a detectable signal no current rule covers | 22 | 37% |
| `deleted_rule` territory — looping / blind search / thin verification | 7 | 12% |
| `kept_rule_gap` — a kept rule's pattern fired but under threshold or in an unrecognised shape | 3 | 5% |

**Headline: 53% (32/60) of silent failures contained some process signal;
47% had none** -- those runs failed for reasons no step-level rule can see
(the fix was simply wrong, or the implementation didn't match the hidden
test's contract, while every step looked healthy).

## The dominant new shape: patch pollution

17 of the 22 `new_shape` cases are the same thing: **the final patch contains
files that are not the fix** -- self-written `reproduce_issue.py` /
`debug_*.py` / `comprehensive_test.py` scripts, `.openhands/TASKS.md` tool
artifacts, `issue.md` copies, and empty `=1.16.0`-style files from unquoted
`pip install` arguments. This is artifact-checkable (a file-set/pattern test
against the final diff, the same grounding as `termination_anomaly`), cheap,
and currently invisible. Three sub-shapes beyond pollution were also observed:

- **test tampering** (2 cases): the agent edited the repo's *existing* tests to
  match its own implementation, making self-verification meaningless;
- **destructive patch** (1 case): the patch *deletes* a core source file
  rather than modifying it -- the current rules check "patch touches no
  source" but not "patch deletes source";
- **self-certification loop** (recurring as a note): self-written tests all
  pass while the real evaluation fails -- detectable only weakly.

## Reading the recall number honestly

- The 80% silence rate on failed runs is itself the most important number:
  SWE-bench failures are dominated by "the fix was wrong" -- a category that
  is invisible at the process level by construction.
- The 3 `kept_rule_gap` cases confirm the kept rules' thresholds bind: two
  `edit_error` under-threshold rejections, one rationalised-away failing test
  (`submit_despite_failure` did not fire because the "failure" was masked as
  environment trouble).
- The 7 `deleted_rule` cases are the price of the pruning round: the looping /
  blind-search / thin-verification shapes still occur (~12% of silent
  failures) and were accepted as false-negative territory when those rules
  were removed for low precision.
- The analyst is the same kind of model that produced the runs' reviews;
  a human pass on a subsample would strengthen the estimate.

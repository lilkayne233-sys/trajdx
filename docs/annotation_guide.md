# Annotation guide

The detectors make claims about agent behaviour. This guide defines when a claim
is **valid**, so that two annotators — a language model and a human — can label
the same sample and be compared.

Every sample is one `finding`: a detector's verdict on a span of steps, plus two
steps of context on each side. The label is about the **claim**, not about
whether the run eventually succeeded: a detector can be right about a trajectory
that still passed, and wrong about one that failed.

## Output

For each finding, emit exactly one verdict:

| verdict | meaning |
|---|---|
| `valid` | The evidence in the context genuinely shows the claimed behaviour. |
| `invalid` | The behaviour did not occur, or occurred but is clearly justified. |
| `uncertain` | The context is insufficient to decide. |

Rule of thumb: **`valid` requires that a reasonable engineer looking at this span
would agree the agent wasted effort or skipped necessary work.** If you have to
argue for it, it is `invalid`.

---

## Per-detector criteria

### `execution_loop` — pattern `exact`
*Claim: the same action was repeated within a few steps and produced the same result.*

- `valid` — the repeated steps have the same effect and the environment did not
  change between them. Re-running an identical failing command is the archetype.
- `invalid` — the repetition is justified: the file changed in between, the
  command is a routine progress check (`ls`, `git status`, `pwd`), or the second
  run is *verification* of a fix that was just applied.
- Watch for argument scrubbing artefacts: commands that differ only in a commit
  hash, temp path or line number may look identical in the `action` column while
  being genuinely different actions. If the observations differ, it is `invalid`.

### `execution_loop` — pattern `error`
*Claim: different actions kept producing the same error.*

- `valid` — the agent changed what it was doing, the error fingerprint did not
  change, and it made no tangible progress against that error.
- `invalid` — the intervening edits were real progress on a different facet, or
  the error is expected noise (e.g. a negative test case that is meant to fail).

### `execution_loop` — pattern `oscillation`
*Claim: the agent alternated between two actions repeatedly.*

- `valid` — the alternation is a thrash pattern with no state change.
- `invalid` — the alternation is a legitimate read-then-edit rhythm.

### `blind_search` — pattern `redundant_read`
*Claim: a file was viewed repeatedly and returned identical content.*

- `valid` — the repeated views returned the same content and the agent learned
  nothing.
- `invalid` — the file had been edited between views, so the second look was
  justified, or the views are of *different* regions of a large file.

### `blind_search` — pattern `read_without_edit`
*Claim: exploration ran far past a reasonable budget without producing an edit.*

- `valid` — the streak looks like flailing: unrelated files, repeated searching
  for the same thing, no narrowing of the search space.
- `invalid` — the inspection is systematic and convergent (reading a module, its
  tests, then its callers before making a targeted change).

### `verification_gap` — pattern `never_verified`
*Claim: the agent edited code and submitted without ever running the tests.*

- `valid` — no test execution appears anywhere in the trajectory.
- `invalid` — a test was run in a form the detector failed to recognise (a custom
  script, `pytest` invoked through a wrapper, a CI command). Be strict: the claim
  is about test execution, and if you can see tests running it is `invalid`.

### `verification_gap` — pattern `stale_verification`
*Claim: the last test ran well before the final edit.*

- `valid` — meaningful code changes were made after the last test run and were
  never checked.
- `invalid` — the later edits are cosmetic (comments, formatting, docstrings), or
  the agent re-ran an equivalent check in another form.

### `verification_gap` — pattern `low_test_intensity`
*Claim: many edits were made against very few test executions.*

- `valid` — the edit-to-test ratio really is lopsided and the edits are
  substantive.
- `invalid` — the "edits" are a single logical change applied in several tool
  calls (e.g. creating a file then appending to it), which inflates the count.

### `termination_anomaly` — pattern `iteration_cap` / `no_submit`
*Claim: the run was cut off by the step budget, or ended without submitting.*

- `valid` — the trajectory ends abruptly at the cap, or contains no submission.
- `invalid` — the run submitted normally and the detector misread the status.

### `termination_anomaly` — pattern `patch_ignores_source`
*Claim: the final patch touches only tests or throwaway scripts.*

- `valid` — every patched file is under `tests/` or is a scratch script, so no
  library code changed.
- `invalid` — at least one patched file is real library source.

### `termination_anomaly` — pattern `empty_patch`
*Claim: the run finished with an empty diff.*

- `valid` — the patch really is empty despite the agent having taken steps.
- `invalid` — a patch exists; the field was simply not captured.

### `environment_stuck`
*Claim: setup or dependency commands kept failing.*

- `valid` — the same install/setup command failed repeatedly with no successful
  resolution.
- `invalid` — the failures are transient and the agent moved on, or the command
  was not actually an environment operation.

---

## Consistency notes

- **Do not reward intent.** "The agent was trying to fix it" is not evidence that
  the step was productive.
- **Do not penalise failure.** A `valid` execution loop in a run that ultimately
  passed is still `valid`.
- **Use `uncertain` sparingly.** More than ~10% `uncertain` means the context
  window is too small, and should be reported rather than absorbed.

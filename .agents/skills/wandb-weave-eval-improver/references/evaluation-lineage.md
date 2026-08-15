# Evaluation lineage implementation

Use this reference only when implementing or checking the score-neutral lineage
plumbing in this repository.

## Contract

Read lineage from `WEAVE_EVAL_LINEAGE_JSON`. Require one JSON object containing:

```json
{
  "eval.improvement.source_evaluation_id": "source-call-id",
  "eval.improvement.trial_id": "eval-sourceid-20260815010203",
  "eval.improvement.target_metric": "SlideQualityScorer.score",
  "eval.improvement.direction": "maximize",
  "eval.improvement.hypothesis": "One concise falsifiable hypothesis",
  "eval.improvement.change_summary": "Short user-visible summary",
  "eval.improvement.changed_files": ["workspaces/baseline/.agent/skills/pptx-generator/SKILL.md"],
  "git.worktree_id": "eval-sourceid-20260815010203",
  "git.worktree.path": "tmp/eval-worktrees/eval-sourceid-20260815010203",
  "git.branch": "eval-improve/eval-sourceid-20260815010203",
  "git.base_commit": "40-character commit SHA",
  "git.candidate_commit": "40-character commit SHA"
}
```

Reject unknown types, empty strings, missing keys, non-list `changed_files`, and
a `git.candidate_commit` that does not equal `git rev-parse HEAD`. Do not put
API keys, environment values, full prompts, source documents, or absolute local
paths in metadata.

An Evaluation without the environment variable must retain the existing
behavior and name. This keeps normal seminar commands backward compatible.

## `eval/agent_model.py`

Add optional immutable lineage fields to `SlideAgentModel` so the Weave Model
object makes the candidate understandable in Compare Evaluations:

```python
class SlideAgentModel(weave.Model):
    variant: str
    improvement_trial_id: str | None = None
    source_evaluation_id: str | None = None
    candidate_commit: str | None = None
    change_summary: str | None = None
```

Do not pass these fields into the agent prompt or change `predict` behavior.

## `eval/run_eval.py`

Add a parser function that returns `{}` when the variable is absent and a
validated dictionary when present. Keep the required keys in one constant so
tests can exercise the contract.

Follow this canonical structure because `trial_workspace.py start-evaluation`
validates the initial lineage patch before any paid call:

- Add exactly the direct imports `json`, `os`, and `subprocess`.
- Add exactly the module constants `REQUIRED_LINEAGE_KEYS` and
  `TRACE_LINEAGE_KEYS`.
- Add exactly these three top-level helpers:
  `load_evaluation_lineage`, `evaluation_display_name`,
  and `trace_lineage_attributes`.
- Inside `run_evaluation`, assign `lineage = load_evaluation_lineage()` and
  `evaluation_name = evaluation_display_name(variant, lineage)` with those
  exact variable names. Extend `build_eval_context` with one keyword-only
  `lineage` argument, unpack exactly
  `**trace_lineage_attributes(lineage)` into its existing returned dictionary,
  and pass `lineage=lineage` plus `evaluation_name=evaluation_name` at every
  call site.
- Pass all four Model fields explicitly as `lineage.get(...)`; do not use
  `**kwargs` expansion.
- Store the display name in `evaluation_name` and pass
  `name=evaluation_name, eval_attributes=lineage` to the one existing
  `EvaluationLogger` constructor.
- Do not change existing Dataset/scorer assignments, protected calls, loop
  headers, or non-lineage constructor arguments.
- Add lineage tests only as the new file
  `eval/tests/test_evaluation_lineage.py`; do not edit existing evaluation
  tests.
- Initialize one `evaluation_id: str | None = None`; inside the existing
  prediction context assign
  `current_evaluation_id = prediction.evaluate_call.id`, use one `if` to set
  the first ID and a second `if` to raise when a later ID differs, return that
  ID as the third result, and print it under exactly one
  `if evaluation_id:` block with the `[evaluation_id]` label.

All other `eval/` paths, including package directories that could shadow
`dataset.py` or `scorers.py`, are rejected. After this first addition, every
path under `eval/` is frozen for improvement trials. For the initial addition,
the validator removes only the canonical lineage nodes from the candidate AST
and requires the entire remaining AST to equal the base, including control
flow and statement order.

When lineage is present:

1. Instantiate `SlideAgentModel` with the four model fields above.
2. Set the display name to
   `improve:<target-metric>:<trial-id>`; otherwise retain the variant name.
3. Pass the complete dictionary to
   `weave.EvaluationLogger(eval_attributes=lineage)`.
4. Capture `prediction.evaluate_call.id` from the first prediction, assert that
   every subsequent prediction has the same ID, and return/print it as
   `[evaluation_id] <id>` beside the existing Evaluation URL.
5. Add only scalar, trace-safe lineage values to the context forwarded to the
   TypeScript agent.

Map the trace-safe values under the existing `weave.eval.*` namespace:

```python
TRACE_LINEAGE_KEYS = {
    "eval.improvement.source_evaluation_id":
        "weave.eval.improvement.source_evaluation_id",
    "eval.improvement.trial_id": "weave.eval.improvement.trial_id",
    "eval.improvement.target_metric": "weave.eval.improvement.target_metric",
    "eval.improvement.direction": "weave.eval.improvement.direction",
    "eval.improvement.change_summary": "weave.eval.improvement.change_summary",
    "git.candidate_commit": "weave.eval.improvement.candidate_commit",
}
```

Do not forward `changed_files` because the current TypeScript
`EvaluationTraceContext` accepts scalar `weave.eval.*` attributes only. The
root Evaluation still receives the complete `eval_attributes` dictionary.

## Evaluation identity and local state

The candidate Evaluation ID is the `Evaluation.evaluate` root Call ID. Do not
substitute the trace ID, conversation ID, `predict_and_score` ID, or W&B run ID.
Record that root Call ID and `eval_logger.ui_url` with
`trial_workspace.py record-evaluation`.

`record-evaluation` reads the remote root Call and verifies the complete
reserved lineage map before persisting the ID. A missing or mismatched
attribute is an evaluation-lineage failure, not a successful trial result.

Call `trial_workspace.py start-evaluation` immediately before the Evaluation
command. This persists `attempted=true` and the candidate commit before any
remote write or paid model call. A process interruption therefore consumes the
single trial; resume by recording failure, not by launching again. Reservation
is protected by a per-trial file lock. If the candidate worktree changes after
reservation, keep any emitted Evaluation ID but classify the trial as
`inconclusive`.

The lineage is intentionally redundant:

- Evaluation display name gives a short comparison label.
- Evaluation root attributes retain the full hypothesis and Git identity.
- Model fields make the candidate code identity visible from the Model object.
- Agent spans carry source/candidate linkage for row-level trace debugging.
- The ignored local trial JSON points back to the retained worktree.

## Tests and contract freeze

Add unit tests for absent metadata, every missing/invalid field, commit mismatch,
display-name construction, Model fields, one stable root Evaluation ID, and
trace-safe propagation. Mock Weave and subprocess boundaries; do not call judge
models or publish objects.

Before the candidate Evaluation, prove that the lineage-only patch does not
change Dataset selection, scorer construction/order, judge model, scorer prompt,
thresholds, model inputs/outputs, or aggregation. Abort on any such change.

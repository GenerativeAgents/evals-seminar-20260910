---
name: wandb-weave-eval-improver
description: Analyze one existing W&B Weave Evaluation, identify its dominant controllable failure, implement one falsifiable AI-agent improvement in an isolated Git worktree, run exactly one candidate evaluation, and compare the result with reproducible Evaluation-to-commit lineage. Use for this repository when a user asks to improve an agent metric, investigate a failing Weave Evaluation, or automate one bounded evaluate-improve-reevaluate cycle.
---

# W&B Weave Eval Improver

Run one evidence-driven improvement trial without changing the user's current
worktree. Keep the evaluation contract fixed, preserve the candidate worktree,
and make the source Evaluation, hypothesis, code commit, and candidate
Evaluation mutually traceable.

## Required companion skill

Before querying W&B or forming a hypothesis, read and follow all three files:

1. `../wandb-primary/SKILL.md`
2. `../wandb-primary/references/WEAVE_SDK.md`
3. `../wandb-primary/references/HYPOTHESIS_GENERATION.md`

Use `wandb-primary` helpers and bounded-query rules. Treat Weave calls as data:
inspect structure, calculate summaries, and never dump full source documents,
model outputs, or traces into context.

## Hard boundaries

- Run exactly one candidate Evaluation per invocation. Do not start a second
  trial, even when the first candidate regresses.
- Never edit, stash, reset, checkout, commit, or merge in the user's original
  worktree. Do not delete any worktree or branch.
- Never merge or cherry-pick the candidate automatically.
- Never change the Dataset, Dataset rows, scorers, scorer prompts, judge model,
  thresholds, aggregation, expected test values, or archived results to improve
  the score.
- Allow only score-neutral lineage plumbing in `eval/run_eval.py` and
  `eval/agent_model.py`. After adding it, treat those semantics as frozen.
- Do not run a paid Evaluation until its dataset size, judge/model use, target
  metric, direction, and one-trial limit are known. Ask only for information
  that repository and Evaluation inspection cannot discover.
- Preserve the candidate worktree and local trial record on success or failure.

## Inputs and readiness gate

Obtain these inputs before mutation:

- Source Evaluation ID or W&B call URL.
- Use all discovered numeric and boolean quality metrics by default. For this
  repository, start with:
  `tool_correctness.passed` (maximize),
  `summarization.summarization_eval_score` (maximize),
  `summarization.is_entity_dense` (maximize),
  `summarization.entity_density` (maximize),
  `hallucination_free.has_hallucination` (minimize),
  `SlideQualityScorer.score` (maximize), and
  `SlideQualityScorer.passed` (maximize). Confirm the exact leaf names from the
  source Evaluation because SDK versions may change them. Do not ask the user
  to choose among them unless the user explicitly requests a narrower scope.
  Select one primary target from the dominant failure for the hypothesis and
  lineage; treat every other discovered quality metric as a guardrail.
- Base Git ref. Prefer `git.candidate_commit`, then `git.commit`, from source
  Evaluation metadata. If neither exists, ask for a base ref; never silently
  equate the current `HEAD` with the source Evaluation.
- Evaluation command and variant. Infer them from this repository and source
  Evaluation when possible.

Require a completed `Evaluation.evaluate` root, a resolvable Dataset ref,
non-empty prediction rows, and the expected scorer set. If the baseline is
running, incomplete, missing scores, or dominated by infrastructure errors,
stop the quality-improvement flow and report the evaluation reliability issue.

## Workflow

### 1. Inspect the source Evaluation

State the expected healthy behavior and metric relationships before querying.
Then run:

```bash
uv run .agents/skills/wandb-weave-eval-improver/scripts/inspect_evaluation.py \
  EVALUATION_ID_OR_URL --project ENTITY/PROJECT
```

Use `WANDB_ENTITY` and `WANDB_PROJECT` instead of `--project` when available.
For failed or worst rows, rerun with `--include-agent-traces`; keep the row and
trace limits bounded.

If ordinary linked calls are empty but a row contains `conversation_id`, use
`wandb-primary/scripts/weave_agent_ops.py conversation` to read that separate
Weave Agents data plane. Do not treat an empty `get_calls()` result as proof
that the Agent Trace is absent.

Separate these failure classes:

- Evaluation infrastructure: exceptions, missing predictions or scores,
  unresolved refs, timeouts, or incomplete status.
- Agent behavior: incorrect tool use, unsupported claims, poor summary or slide
  quality, malformed output, or avoidable latency/cost.

Identify the most specific controllable anomaly. Name the affected example,
metric, observed value, and scorer reason. Inspect its linked Agent Trace before
claiming a mechanism. Rank the default metrics by failure frequency and
severity, then choose the most dominant controllable one as the single primary
target. Keep all remaining metrics in the before/after comparison.

### 2. Define one falsifiable improvement

Write the hypothesis before editing:

```text
Anomaly: <specific row/behavior and evidence>
Mechanism: <why the agent produced it>
Change: <one primary variable to modify>
Expected result: <target metric moves in direction X while guardrails hold>
Alternative if false: <next mechanism to investigate, but do not run it now>
```

Prefer the smallest surface supported by evidence:

1. `workspaces/<variant>/AGENTS.md`, the variant's `.agent/skills/`, or the
   system prompt.
2. Tool selection, validation, retries, or structured-output orchestration.
3. Application implementation or model/tool settings directly implicated by
   the trace.

Do not make broad cleanup changes or combine unrelated fixes.

### 3. Create the isolated worktree

Run from the original repository:

```bash
uv run .agents/skills/wandb-weave-eval-improver/scripts/trial_workspace.py \
  create --source-evaluation-id SOURCE_ID --source-evaluation-url SOURCE_URL \
  --base-ref BASE_REF \
  --target-metric TARGET_METRIC --direction maximize \
  --hypothesis "ONE FALSIFIABLE HYPOTHESIS" \
  --change-summary "ONE SHORT CHANGE SUMMARY"
```

The command creates:

- Trial ID: `eval-<source-id-short>-<UTC timestamp>`
- Branch: `eval-improve/<trial-id>`
- Worktree: `tmp/eval-worktrees/<trial-id>/`
- State: `tmp/eval-improvements/<trial-id>.json`

Use the returned worktree as the working directory for every edit, test,
commit, and Evaluation command. Re-run the `status` subcommand before the
Evaluation. Stop if the original worktree status changed.

Prepare dependencies inside the candidate worktree, not through the original
worktree's `.venv` or `node_modules`. For this repository use the locked setup
(`uv sync --frozen` and `npm ci`) when those directories are absent. Link only
the ignored `.env` file from the original worktree when credentials are needed;
never copy or log its contents, and never commit the link.

### 4. Implement and commit the candidate

Make only the hypothesized agent change. If lineage plumbing is absent, read
`references/evaluation-lineage.md` completely and add only that score-neutral
instrumentation plus its tests.

Run the repository's relevant unit/type checks in the candidate worktree.
Review `git diff`, verify the protected evaluation surfaces are unchanged, and
create a local candidate commit before evaluation. Require a clean tracked-file
status after the commit. `start-evaluation` mechanically rejects changes to the
entire `eval/` tree; once lineage exists, that tree remains frozen. During the
one permitted initial lineage addition, only `eval/run_eval.py`,
`eval/agent_model.py`, and a new
`eval/tests/test_evaluation_lineage.py` are allowed. The preflight compares the
candidate AST with the base after removing only canonical lineage nodes; all
remaining control flow, statement order, assignments, calls, loops, and
constructor arguments must match. Do not include generated outputs, `.env`, or
secrets.

### 5. Run one candidate Evaluation

Build `WEAVE_EVAL_LINEAGE_JSON` with the exact committed state and the schema in
`references/evaluation-lineage.md`. Verify `git.candidate_commit` equals the
candidate worktree's `HEAD`, then run the discovered Evaluation command once
from that worktree.

Use the `observed.lineage` object printed by `trial_workspace.py status` as the
environment value rather than retyping commit IDs or changed files.

Immediately before launching the command, consume the one allowed attempt:

```bash
uv run .agents/skills/wandb-weave-eval-improver/scripts/trial_workspace.py \
  start-evaluation --trial-id TRIAL_ID
```

If the process is interrupted after this point, treat the trial as consumed.
Record a failure when execution resumes; never launch the Evaluation again.
The reservation uses a per-trial file lock, so concurrent callers cannot both
consume or launch the same trial.

Capture the emitted candidate Evaluation ID and URL. Record them locally:

```bash
uv run .agents/skills/wandb-weave-eval-improver/scripts/trial_workspace.py \
  record-evaluation --trial-id TRIAL_ID \
  --evaluation-id CANDIDATE_ID --evaluation-url CANDIDATE_URL
```

This command reads the candidate `Evaluation.evaluate` root and requires every
reserved lineage attribute to match before saving the ID. If the candidate
worktree HEAD or clean status changed after reservation, it preserves the
remote ID but marks the trial `inconclusive` and forbids a later success label.

If execution fails, record the error in the trial JSON, report it, and stop.
Do not retry as a hidden second trial.

```bash
uv run .agents/skills/wandb-weave-eval-improver/scripts/trial_workspace.py \
  record-failure --trial-id TRIAL_ID --error "BOUNDED ERROR SUMMARY"
```

### 6. Compare and stop

Inspect the candidate with the same helper and limits as the source. Before
interpreting scores, verify identical Dataset ref, scorer identities/versions,
judge settings, and evaluation semantics.

Compare source and candidate by stable example ID:

- Target metric paired delta in the requested direction.
- Every other numeric/boolean quality metric as a guardrail.
- Evaluation and descendant error counts.
- Latency, tokens, and cost when present.

Classify the single trial:

- `improved`: target moves in the requested direction, the contract matches,
  and no material guardrail or reliability regression appears.
- `regressed`: target moves the wrong way or errors increase.
- `inconclusive`: target is flat, data is truncated/missing, contract differs,
  guardrails conflict with the target, or the candidate worktree changed after
  `start-evaluation`.

Update the trial record with the classification and compact paired comparison.
End the invocation; do not implement the alternative hypothesis.

```bash
uv run .agents/skills/wandb-weave-eval-improver/scripts/trial_workspace.py \
  record-comparison --trial-id TRIAL_ID --result-status improved \
  --comparison-json '{"target_delta": 0.1, "guardrails": {}}'
```

## Required final report

Report all of the following:

- Source and candidate Evaluation IDs and URLs.
- Trial ID, retained worktree path, branch, base commit, and candidate commit.
- Anomaly, mechanism, one-variable change, and changed files.
- A compact paired metric table including the target and guardrails.
- Evaluation errors, latency/tokens/cost when available.
- `improved`, `regressed`, or `inconclusive`, with the evidence for that label.
- The exact command for inspecting the retained trial state.

Never imply that the candidate was merged or accepted.

## Bundled commands

- `scripts/inspect_evaluation.py` performs bounded, read-only Evaluation and
  linked-trace inspection and prints JSON.
- `scripts/trial_workspace.py` creates, checks, reserves, and records isolated
  one-shot trials. It intentionally has no cleanup or merge operation.
- `references/evaluation-lineage.md` defines the repo-specific Weave logging
  contract and implementation pattern.

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import inspect_evaluation  # noqa: E402
import trial_workspace  # noqa: E402


SKILL_ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Skill Test")
    _git(repo, "config", "user.email", "skill-test@example.com")
    (repo / ".gitignore").write_text("tmp/*\n", encoding="utf-8")
    (repo / "seed.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "seed.txt")
    _git(repo, "commit", "-qm", "initial")
    return repo


def _init_eval_repo(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path)
    (repo / "eval" / "tests").mkdir(parents=True)
    (repo / "eval" / "run_eval.py").write_text(
        """import weave
from agent_model import SlideAgentModel
from scorers import build_scorers


def build_eval_context(prediction, *, example_id, evaluation_name):
    return {"example_id": example_id, "evaluation_name": evaluation_name}


async def run_evaluation(*, variant, dataset):
    model = SlideAgentModel(variant=variant)
    scorers = build_scorers()
    eval_logger = weave.EvaluationLogger(
        name=variant, model=model, dataset=dataset, scorers=scorers
    )
    errors = 0
    for row in dataset.rows:
        eval_context = build_eval_context(
            prediction, example_id=str(row), evaluation_name=variant
        )
        for scorer in scorers:
            await model.predict(row=row, scorer=scorer)
    return eval_logger.ui_url, errors
""",
        encoding="utf-8",
    )
    (repo / "eval" / "agent_model.py").write_text(
        """import weave


class SlideAgentModel(weave.Model):
    variant: str

    async def predict(self, row, scorer):
        return {"row": row, "scorer": scorer}
""",
        encoding="utf-8",
    )
    (repo / "eval" / "tests" / "test_eval.py").write_text(
        "def test_contract():\n    assert True\n", encoding="utf-8"
    )
    _git(repo, "add", "eval")
    _git(repo, "commit", "-qm", "add baseline evaluation")
    return repo


def _commit_valid_lineage(worktree: Path, *, tamper_scorers: bool = False) -> None:
    scorer_line = "scorers = []" if tamper_scorers else "scorers = build_scorers()"
    (worktree / "eval" / "run_eval.py").write_text(
        f"""import weave
import json
import os
import subprocess
from agent_model import SlideAgentModel
from scorers import build_scorers

REQUIRED_LINEAGE_KEYS = (
    "eval.improvement.source_evaluation_id",
    "eval.improvement.trial_id",
    "eval.improvement.target_metric",
    "eval.improvement.direction",
    "eval.improvement.hypothesis",
    "eval.improvement.change_summary",
    "eval.improvement.changed_files",
    "git.worktree_id",
    "git.worktree.path",
    "git.branch",
    "git.base_commit",
    "git.candidate_commit",
)
TRACE_LINEAGE_KEYS = {{
    "eval.improvement.source_evaluation_id": "weave.eval.improvement.source_evaluation_id",
    "eval.improvement.trial_id": "weave.eval.improvement.trial_id",
    "eval.improvement.target_metric": "weave.eval.improvement.target_metric",
    "eval.improvement.direction": "weave.eval.improvement.direction",
    "eval.improvement.change_summary": "weave.eval.improvement.change_summary",
    "git.candidate_commit": "weave.eval.improvement.candidate_commit",
}}


def load_evaluation_lineage():
    lineage = json.loads(os.environ["WEAVE_EVAL_LINEAGE_JSON"])
    head = subprocess.run(["git", "rev-parse", "HEAD"], check=True)
    if not head:
        raise RuntimeError("missing git head")
    return lineage


def evaluation_display_name(variant, lineage):
    return (
        f"improve:{{lineage['eval.improvement.target_metric']}}:"
        f"{{lineage['eval.improvement.trial_id']}}"
    )


def trace_lineage_attributes(lineage):
    return {{target: lineage.get(source) for source, target in TRACE_LINEAGE_KEYS.items()}}


def build_eval_context(prediction, *, example_id, evaluation_name, lineage):
    return {{
        "example_id": example_id,
        "evaluation_name": evaluation_name,
        **trace_lineage_attributes(lineage),
    }}


async def run_evaluation(*, variant, dataset):
    lineage = load_evaluation_lineage()
    evaluation_name = evaluation_display_name(variant, lineage)
    model = SlideAgentModel(
        variant=variant,
        improvement_trial_id=lineage.get("eval.improvement.trial_id"),
        source_evaluation_id=lineage.get("eval.improvement.source_evaluation_id"),
        candidate_commit=lineage.get("git.candidate_commit"),
        change_summary=lineage.get("eval.improvement.change_summary"),
    )
    {scorer_line}
    eval_logger = weave.EvaluationLogger(
        name=evaluation_name,
        model=model,
        dataset=dataset,
        scorers=scorers,
        eval_attributes=lineage,
    )
    errors = 0
    evaluation_id: str | None = None
    for row in dataset.rows:
        current_evaluation_id = prediction.evaluate_call.id
        if evaluation_id is None:
            evaluation_id = current_evaluation_id
        if evaluation_id != current_evaluation_id:
            raise RuntimeError("Evaluation root changed")
        eval_context = build_eval_context(
            prediction,
            example_id=str(row),
            evaluation_name=evaluation_name,
            lineage=lineage,
        )
        for scorer in scorers:
            await model.predict(row=row, scorer=scorer)
    if evaluation_id:
        print(f"[evaluation_id] {{evaluation_id}}")
    return eval_logger.ui_url, errors, evaluation_id
""",
        encoding="utf-8",
    )
    (worktree / "eval" / "agent_model.py").write_text(
        """import weave


class SlideAgentModel(weave.Model):
    variant: str
    improvement_trial_id: str | None = None
    source_evaluation_id: str | None = None
    candidate_commit: str | None = None
    change_summary: str | None = None

    async def predict(self, row, scorer):
        return {"row": row, "scorer": scorer}
""",
        encoding="utf-8",
    )
    (worktree / "eval" / "tests" / "test_evaluation_lineage.py").write_text(
        "def test_lineage_contract():\n    assert True\n", encoding="utf-8"
    )


def _create_args(repo: Path, *, timestamp: str = "20260815010203"):
    return SimpleNamespace(
        repo=str(repo),
        source_evaluation_id="019abcde-source",
        source_evaluation_url=(
            "https://wandb.ai/team/project/r/call/019abcde-source"
        ),
        base_ref="HEAD",
        target_metric="SlideQualityScorer.score",
        direction="maximize",
        hypothesis="Dense slides result from missing content limits.",
        change_summary="Add explicit slide-density guidance.",
        timestamp=timestamp,
    )


def _mock_remote_lineage(monkeypatch, state: dict) -> None:
    expected = trial_workspace._expected_lineage(
        state,
        candidate_commit=state["candidate"]["commit"],
        changed_files=state["improvement"]["changed_files"],
    )
    monkeypatch.setattr(
        trial_workspace,
        "_fetch_evaluation_attributes",
        lambda _evaluation_id, _evaluation_url: expected,
    )


def test_default_prompt_lists_all_metrics_and_change_targets():
    metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(
        encoding="utf-8"
    )
    for metric in (
        "tool_correctness.passed",
        "summarization.summarization_eval_score",
        "summarization.is_entity_dense",
        "summarization.entity_density",
        "hallucination_free.has_hallucination",
        "SlideQualityScorer.score",
        "SlideQualityScorer.passed",
    ):
        assert metric in metadata
    for change_target in (
        "workspaces/<variant>/AGENTS.md",
        "workspaces/<variant>/.agent/skills/",
        "system prompt",
        "agent orchestration",
        "model/tool設定",
    ):
        assert change_target in metadata
    assert "$wandb-weave-eval-improver" in metadata


def test_parse_locator_accepts_call_url_and_bare_id():
    project, call_id = inspect_evaluation.parse_locator(
        "https://wandb.ai/my-team/my-project/r/call/019abcde-call",
        None,
    )
    assert project == "my-team/my-project"
    assert call_id == "019abcde-call"

    assert inspect_evaluation.parse_locator(
        "019abcde-call", "my-team/my-project"
    ) == ("my-team/my-project", "019abcde-call")


def test_parse_locator_requires_project_for_bare_id():
    with pytest.raises(inspect_evaluation.InspectionError, match="requires"):
        inspect_evaluation.parse_locator("019abcde-call", None)


def test_summarize_evaluation_reports_failures_contract_and_linked_trace():
    started = datetime(2026, 8, 15, tzinfo=timezone.utc)
    root = SimpleNamespace(
        id="evaluation-call",
        func_name="Evaluation.evaluate",
        op_name="weave:///team/project/op/Evaluation.evaluate:hash",
        ui_url="https://wandb.ai/team/project/r/call/evaluation-call",
        display_name="baseline",
        started_at=started,
        ended_at=started + timedelta(seconds=15),
        exception=None,
        attributes={"git.commit": "abc"},
        summary={
            "weave": {"status": "success"},
            "status_counts": {"success": 8, "error": 0},
            "usage": {
                "judge": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                }
            },
        },
        inputs={
            "self": {
                "dataset": "weave:///team/project/object/papers:dataset-hash",
                "metadata": {
                    "scorers": ["tool_correctness", "SlideQualityScorer"]
                },
            },
            "model": {"variant": "baseline"},
        },
    )
    prediction = SimpleNamespace(
        id="prediction-call",
        op_name="weave:///team/project/op/Evaluation.predict_and_score:hash",
        started_at=started,
        ended_at=started + timedelta(seconds=10),
        exception=None,
        summary={"weave": {"status": "success"}},
        inputs={"example": {"arxiv_id": "1706.03762"}},
        output={
            "output": {"conversation_id": "baseline:thread-1"},
            "scores": {
                "tool_correctness": {"passed": True},
                "SlideQualityScorer": {
                    "score": 0.4,
                    "passed": False,
                    "reason": "The slide is too dense.",
                },
            }
        },
    )
    trace = SimpleNamespace(
        id="agent-trace",
        op_name="weave:///team/project/op/slide-generator:hash",
        started_at=started,
        ended_at=started + timedelta(seconds=9),
        summary={"weave": {"status": "success"}},
    )
    scorer = SimpleNamespace(
        op_name="weave:///team/project/op/SlideQualityScorer.score:scorer-hash",
        inputs={
            "self": {
                "judge_model": "openrouter/openai/gpt-5.4",
                "threshold": 0.5,
                "prompt": "private scorer prompt",
            }
        },
    )

    result = inspect_evaluation.summarize_evaluation(
        root,
        [prediction],
        total_predictions=3,
        linked_traces={"prediction-call": [trace]},
        scorer_calls=[scorer],
    )

    assert result["evaluation"]["id"] == "evaluation-call"
    assert result["evaluation"]["usage"]["total_tokens"] == 15
    assert result["contract"]["dataset_ref"].endswith("papers:dataset-hash")
    assert result["contract"]["scorer_ops"] == [
        {
            "op_name": "SlideQualityScorer.score",
            "op_ref": "weave:///team/project/op/SlideQualityScorer.score:scorer-hash",
            "judge_model": "openrouter/openai/gpt-5.4",
            "threshold": 0.5,
            "prompt_sha256": (
                "cf635c37846904572b21e0cd5a2cf5277492b694fd691af538e39eca4929e8b2"
            ),
        }
    ]
    assert result["prediction_count"] == 3
    assert result["returned_prediction_count"] == 1
    assert result["truncated"] is True
    assert result["failure_count_in_returned_rows"] == 1
    row = result["rows"][0]
    assert row["example_id"] == "1706.03762"
    assert row["conversation_id"] == "baseline:thread-1"
    assert row["scores"]["SlideQualityScorer.score"] == 0.4
    assert row["scores"]["SlideQualityScorer.reason"] == "The slide is too dense."
    assert row["failed_checks"] == ["SlideQualityScorer.passed"]
    assert row["linked_agent_traces"][0]["call_id"] == "agent-trace"


def test_summarize_evaluation_rejects_non_evaluation_call():
    call = SimpleNamespace(func_name="other", op_name="other")
    with pytest.raises(inspect_evaluation.InspectionError, match="not an"):
        inspect_evaluation.summarize_evaluation(call, [])


def test_create_trial_preserves_dirty_primary_status_and_rejects_duplicate(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "user-note.txt").write_text("keep me\n", encoding="utf-8")
    before = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")

    result = trial_workspace.create_trial(_create_args(repo))

    after = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    assert after == before
    assert result["trial_id"] == "eval-019abcdesour-20260815010203"
    assert result["worktree"]["relative_path"].startswith("tmp/eval-worktrees/")
    assert Path(result["worktree"]["path"]).is_dir()
    assert Path(result["state_path"]).is_file()
    assert result["worktree"]["base_commit"] == _git(
        repo, "rev-parse", "HEAD"
    ).strip()

    with pytest.raises(trial_workspace.TrialError, match="committed change"):
        trial_workspace.start_evaluation(
            SimpleNamespace(repo=str(repo), trial_id=result["trial_id"])
        )

    with pytest.raises(trial_workspace.TrialError, match="already exists"):
        trial_workspace.create_trial(_create_args(repo))


def test_create_trial_requires_git_ignored_trial_paths(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text("# tmp is intentionally tracked\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "remove tmp ignore")

    with pytest.raises(trial_workspace.TrialError, match="must be ignored"):
        trial_workspace.create_trial(_create_args(repo))

    assert not (repo / "tmp").exists()


def test_status_and_record_evaluation_capture_candidate_commit(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    trial_id = created["trial_id"]
    worktree = Path(created["worktree"]["path"])

    status = trial_workspace.trial_status(
        SimpleNamespace(repo=str(repo), trial_id=trial_id)
    )
    assert status["observed"]["worktree_clean"] is True
    assert status["observed"]["primary_status_unchanged"] is True
    assert status["observed"]["lineage"]["git.candidate_commit"] == status[
        "observed"
    ]["head"]
    assert status["observed"]["lineage"]["eval.improvement.direction"] == "maximize"

    (worktree / "seed.txt").write_text("candidate\n", encoding="utf-8")
    _git(worktree, "add", "seed.txt")
    _git(worktree, "commit", "-qm", "candidate")
    candidate_commit = _git(worktree, "rev-parse", "HEAD").strip()

    with pytest.raises(trial_workspace.TrialError, match="start-evaluation"):
        trial_workspace.record_evaluation(
            SimpleNamespace(
                repo=str(repo),
                trial_id=trial_id,
                evaluation_id="019candidate-call",
                evaluation_url=(
                    "https://wandb.ai/team/project/r/call/019candidate-call"
                ),
                comparison_json=None,
            )
        )

    started = trial_workspace.start_evaluation(
        SimpleNamespace(repo=str(repo), trial_id=trial_id)
    )
    assert started["status"] == "evaluation-started"
    assert started["candidate"]["attempted"] is True
    assert started["candidate"]["commit"] == candidate_commit
    with pytest.raises(trial_workspace.TrialError, match="already has"):
        trial_workspace.start_evaluation(
            SimpleNamespace(repo=str(repo), trial_id=trial_id)
        )

    _mock_remote_lineage(monkeypatch, started)
    recorded = trial_workspace.record_evaluation(
        SimpleNamespace(
            repo=str(repo),
            trial_id=trial_id,
            evaluation_id="019candidate-call",
            evaluation_url="https://wandb.ai/team/project/r/call/019candidate-call",
            result_status="evaluated",
            comparison_json=None,
        )
    )
    assert recorded["status"] == "evaluated"
    assert recorded["candidate"]["commit"] == candidate_commit
    assert recorded["candidate"]["evaluation_id"] == "019candidate-call"
    assert recorded["candidate"]["head_unchanged_at_record"] is True
    assert recorded["candidate"]["worktree_clean_at_record"] is True
    assert recorded["improvement"]["changed_files"] == ["seed.txt"]
    assert recorded["comparison"] is None

    compared = trial_workspace.record_comparison(
        SimpleNamespace(
            repo=str(repo),
            trial_id=trial_id,
            result_status="improved",
            comparison_json=json.dumps({"target_delta": 0.2}),
        )
    )
    assert compared["status"] == "improved"
    assert compared["comparison"] == {"target_delta": 0.2}

    with pytest.raises(trial_workspace.TrialError, match="already has"):
        trial_workspace.record_evaluation(
            SimpleNamespace(
                repo=str(repo),
                trial_id=trial_id,
                evaluation_id="019second-call",
                evaluation_url="https://example.test/second",
                result_status="evaluated",
                comparison_json=None,
            )
        )


def test_record_evaluation_preserves_link_when_original_status_changes(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    worktree = Path(created["worktree"]["path"])
    (worktree / "seed.txt").write_text("candidate\n", encoding="utf-8")
    _git(worktree, "add", "seed.txt")
    _git(worktree, "commit", "-qm", "candidate")
    started = trial_workspace.start_evaluation(
        SimpleNamespace(repo=str(repo), trial_id=created["trial_id"])
    )
    (repo / "changed-after-create.txt").write_text("new\n", encoding="utf-8")

    _mock_remote_lineage(monkeypatch, started)
    recorded = trial_workspace.record_evaluation(
        SimpleNamespace(
            repo=str(repo),
            trial_id=created["trial_id"],
            evaluation_id="019candidate-call",
            evaluation_url="https://example.test/r/call/019candidate-call",
            result_status="evaluated",
            comparison_json=None,
        )
    )
    assert recorded["primary_status_unchanged_at_record"] is False


def test_record_evaluation_rejects_remote_lineage_mismatch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    trial_id = created["trial_id"]
    worktree = Path(created["worktree"]["path"])
    (worktree / "seed.txt").write_text("candidate\n", encoding="utf-8")
    _git(worktree, "add", "seed.txt")
    _git(worktree, "commit", "-qm", "candidate")
    trial_workspace.start_evaluation(
        SimpleNamespace(repo=str(repo), trial_id=trial_id)
    )
    monkeypatch.setattr(
        trial_workspace,
        "_fetch_evaluation_attributes",
        lambda _evaluation_id, _evaluation_url: {},
    )

    with pytest.raises(trial_workspace.TrialError, match="lineage does not match"):
        trial_workspace.record_evaluation(
            SimpleNamespace(
                repo=str(repo),
                trial_id=trial_id,
                evaluation_id="019candidate-call",
                evaluation_url=(
                    "https://wandb.ai/team/project/r/call/019candidate-call"
                ),
            )
        )

    state = json.loads(Path(created["state_path"]).read_text(encoding="utf-8"))
    assert state["status"] == "evaluation-started"
    assert state["candidate"]["evaluation_id"] is None


def test_worktree_change_after_start_is_recorded_as_inconclusive(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    trial_id = created["trial_id"]
    worktree = Path(created["worktree"]["path"])
    (worktree / "seed.txt").write_text("candidate\n", encoding="utf-8")
    _git(worktree, "add", "seed.txt")
    _git(worktree, "commit", "-qm", "candidate")
    started = trial_workspace.start_evaluation(
        SimpleNamespace(repo=str(repo), trial_id=trial_id)
    )
    _mock_remote_lineage(monkeypatch, started)
    (worktree / "changed-after-start.txt").write_text("dirty\n", encoding="utf-8")

    recorded = trial_workspace.record_evaluation(
        SimpleNamespace(
            repo=str(repo),
            trial_id=trial_id,
            evaluation_id="019candidate-call",
            evaluation_url="https://wandb.ai/team/project/r/call/019candidate-call",
        )
    )

    assert recorded["status"] == "inconclusive"
    assert recorded["candidate"]["evaluation_id"] == "019candidate-call"
    assert recorded["candidate"]["worktree_clean_at_record"] is False
    assert recorded["comparison"]["classification"] == "inconclusive"


def test_start_evaluation_rejects_protected_contract_changes(tmp_path):
    repo = _init_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    worktree = Path(created["worktree"]["path"])
    (worktree / "eval" / "scorers").mkdir(parents=True)
    (worktree / "eval" / "scorers" / "__init__.py").write_text(
        "JUDGE_MODEL = 'changed'\n", encoding="utf-8"
    )
    _git(worktree, "add", "eval/scorers/__init__.py")
    _git(worktree, "commit", "-qm", "change protected scorer")

    with pytest.raises(trial_workspace.TrialError, match="protected"):
        trial_workspace.start_evaluation(
            SimpleNamespace(repo=str(repo), trial_id=created["trial_id"])
        )


def test_initial_lineage_patch_preserves_evaluation_contract(tmp_path):
    repo = _init_eval_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    worktree = Path(created["worktree"]["path"])
    assert created["evaluation_contract"]["lineage_plumbing_required"] is True
    _commit_valid_lineage(worktree)
    _git(worktree, "add", "eval")
    _git(worktree, "commit", "-qm", "add evaluation lineage")

    started = trial_workspace.start_evaluation(
        SimpleNamespace(repo=str(repo), trial_id=created["trial_id"])
    )

    assert started["status"] == "evaluation-started"


def test_initial_lineage_patch_rejects_scorer_semantic_change(tmp_path):
    repo = _init_eval_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    worktree = Path(created["worktree"]["path"])
    _commit_valid_lineage(worktree, tamper_scorers=True)
    _git(worktree, "add", "eval")
    _git(worktree, "commit", "-qm", "mix lineage with scorer change")

    with pytest.raises(trial_workspace.TrialError, match="protected assignments"):
        trial_workspace.start_evaluation(
            SimpleNamespace(repo=str(repo), trial_id=created["trial_id"])
        )


def test_initial_lineage_patch_rejects_existing_test_mutation(tmp_path):
    repo = _init_eval_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    worktree = Path(created["worktree"]["path"])
    _commit_valid_lineage(worktree)
    with (worktree / "eval" / "tests" / "test_eval.py").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write("\npytestmark = 'skip-all'\n")
    _git(worktree, "add", "eval")
    _git(worktree, "commit", "-qm", "mutate expected tests")

    with pytest.raises(trial_workspace.TrialError, match="protected eval paths"):
        trial_workspace.start_evaluation(
            SimpleNamespace(repo=str(repo), trial_id=created["trial_id"])
        )


def test_initial_lineage_patch_rejects_unreachable_evaluation_loop(tmp_path):
    repo = _init_eval_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    worktree = Path(created["worktree"]["path"])
    _commit_valid_lineage(worktree)
    runner = worktree / "eval" / "run_eval.py"
    runner.write_text(
        runner.read_text(encoding="utf-8").replace(
            "    for row in dataset.rows:\n",
            "    return eval_logger.ui_url\n"
            "    for row in dataset.rows:\n",
            1,
        ),
        encoding="utf-8",
    )
    _git(worktree, "add", "eval")
    _git(worktree, "commit", "-qm", "make evaluation loop unreachable")

    with pytest.raises(trial_workspace.TrialError, match="control flow"):
        trial_workspace.start_evaluation(
            SimpleNamespace(repo=str(repo), trial_id=created["trial_id"])
        )


def test_initial_lineage_patch_rejects_invalid_model_field(tmp_path):
    repo = _init_eval_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    worktree = Path(created["worktree"]["path"])
    _commit_valid_lineage(worktree)
    model = worktree / "eval" / "agent_model.py"
    model.write_text(
        model.read_text(encoding="utf-8").replace(
            "candidate_commit: str | None = None",
            "candidate_commit: int = 0",
        ),
        encoding="utf-8",
    )
    _git(worktree, "add", "eval")
    _git(worktree, "commit", "-qm", "break model lineage field")

    with pytest.raises(trial_workspace.TrialError, match=r"str \| None"):
        trial_workspace.start_evaluation(
            SimpleNamespace(repo=str(repo), trial_id=created["trial_id"])
        )


def test_concurrent_start_reserves_exactly_one_attempt(tmp_path):
    repo = _init_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    worktree = Path(created["worktree"]["path"])
    (worktree / "seed.txt").write_text("candidate\n", encoding="utf-8")
    _git(worktree, "add", "seed.txt")
    _git(worktree, "commit", "-qm", "candidate")

    command = [
        sys.executable,
        str(SCRIPTS / "trial_workspace.py"),
        "start-evaluation",
        "--repo",
        str(repo),
        "--trial-id",
        created["trial_id"],
    ]
    processes = [
        subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=10) for process in processes]

    assert sorted(process.returncode for process in processes) == [0, 2]
    assert any("already has" in stderr for _stdout, stderr in results)


def test_record_failure_consumes_the_single_attempt(tmp_path):
    repo = _init_repo(tmp_path)
    created = trial_workspace.create_trial(_create_args(repo))
    trial_id = created["trial_id"]
    worktree = Path(created["worktree"]["path"])
    (worktree / "seed.txt").write_text("candidate\n", encoding="utf-8")
    _git(worktree, "add", "seed.txt")
    _git(worktree, "commit", "-qm", "candidate")
    trial_workspace.start_evaluation(
        SimpleNamespace(repo=str(repo), trial_id=trial_id)
    )

    failed = trial_workspace.record_failure(
        SimpleNamespace(
            repo=str(repo),
            trial_id=trial_id,
            evaluation_id=None,
            evaluation_url=None,
            error="runner exited before creating an Evaluation",
        )
    )
    assert failed["status"] == "failed"
    assert failed["candidate"]["attempted"] is True
    assert failed["candidate"]["error"].startswith("runner exited")
    assert failed["candidate"]["head_unchanged_at_record"] is True

    with pytest.raises(trial_workspace.TrialError, match="already has"):
        trial_workspace.record_failure(
            SimpleNamespace(
                repo=str(repo),
                trial_id=trial_id,
                evaluation_id=None,
                evaluation_url=None,
                error="second attempt",
            )
        )

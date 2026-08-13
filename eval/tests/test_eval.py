"""Weaveライブ評価のPython側単体テスト。

judge LLMは呼ばず、litellm・プリセットscorer・subprocessをfakeへ差し替える。
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_model
import dataset as dataset_module
import scorers as scorers_module
from agent_model import run_agent_process
from dataset import build_dataset_rows
from scorers import (
    JUDGE_MODEL,
    SlideQualityScorer,
    hallucination_free,
    summarization,
    tool_correctness,
)

SUCCESS_OUTPUT = {
    "generation_success": True,
    "slide_text": "# タイトル\n## Slide 2 [content] 提案手法\n- ポイント",
    "tool_calls": [{"name": "execute", "args": {}}, {"name": "generate_pptx", "args": {}}],
}

FAILED_OUTPUT = {
    "generation_success": False,
    "slide_text": "",
    "tool_calls": [{"name": "execute", "args": {}}],
    "failure_reason": "generate_pptxの成功結果が得られませんでした。",
}


# ---------------------------------------------------------------------------
# tool_correctness
# ---------------------------------------------------------------------------


def test_tool_correctness_passes_when_all_tools_called():
    result = tool_correctness(
        output=SUCCESS_OUTPUT, expected_tools=["execute", "generate_pptx"]
    )
    assert result == {"passed": True, "missing_tools": []}


def test_tool_correctness_reports_missing_tools():
    result = tool_correctness(
        output=FAILED_OUTPUT, expected_tools=["execute", "generate_pptx"]
    )
    assert result["passed"] is False
    assert result["missing_tools"] == ["generate_pptx"]


# ---------------------------------------------------------------------------
# プリセットscorerへの移譲wrapper
# ---------------------------------------------------------------------------


class FakePresetScorer:
    def __init__(self):
        self.calls = []

    async def score(self, **kwargs):
        self.calls.append(kwargs)
        return {"passed": True, "reason": "fake"}


async def test_summarization_maps_arguments(monkeypatch):
    fake = FakePresetScorer()
    monkeypatch.setattr(scorers_module, "_summarization", fake)
    result = await summarization(output=SUCCESS_OUTPUT, source_text="本文テキスト")
    assert result == {"passed": True, "reason": "fake"}
    assert fake.calls == [
        {"input": "本文テキスト", "output": SUCCESS_OUTPUT["slide_text"]}
    ]


async def test_summarization_skips_judge_without_slide_text(monkeypatch):
    fake = FakePresetScorer()
    monkeypatch.setattr(scorers_module, "_summarization", fake)
    result = await summarization(output=FAILED_OUTPUT, source_text="本文テキスト")
    assert result["passed"] is False
    assert result["failure_reason"] == FAILED_OUTPUT["failure_reason"]
    assert fake.calls == []


async def test_hallucination_free_maps_arguments(monkeypatch):
    fake = FakePresetScorer()
    monkeypatch.setattr(scorers_module, "_hallucination_free", fake)
    result = await hallucination_free(output=SUCCESS_OUTPUT, source_text="本文テキスト")
    assert result == {"passed": True, "reason": "fake"}
    assert fake.calls == [
        {"context": "本文テキスト", "output": SUCCESS_OUTPUT["slide_text"]}
    ]


async def test_hallucination_free_skips_judge_without_slide_text(monkeypatch):
    fake = FakePresetScorer()
    monkeypatch.setattr(scorers_module, "_hallucination_free", fake)
    result = await hallucination_free(output=FAILED_OUTPUT, source_text="本文テキスト")
    assert result["passed"] is False
    assert fake.calls == []


# ---------------------------------------------------------------------------
# SlideQualityScorer
# ---------------------------------------------------------------------------


def _fake_completion_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


async def test_slide_quality_scorer_normalizes_score_and_passes(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_completion_response(
            json.dumps({"score": 7, "reason": "流れが論理的"})
        )

    monkeypatch.setattr(scorers_module.litellm, "acompletion", fake_acompletion)
    scorer = SlideQualityScorer(judge_model=JUDGE_MODEL)
    result = await scorer.score(output=SUCCESS_OUTPUT, source_text="本文テキスト")
    assert result == {"score": 0.7, "passed": True, "reason": "流れが論理的"}
    assert captured["model"] == JUDGE_MODEL
    assert captured["temperature"] == 0
    prompt = captured["messages"][0]["content"]
    assert SUCCESS_OUTPUT["slide_text"] in prompt
    assert "本文テキスト" in prompt


async def test_slide_quality_scorer_fails_below_threshold(monkeypatch):
    async def fake_acompletion(**kwargs):
        return _fake_completion_response(
            json.dumps({"score": 4, "reason": "詰め込みが多い"})
        )

    monkeypatch.setattr(scorers_module.litellm, "acompletion", fake_acompletion)
    scorer = SlideQualityScorer(judge_model=JUDGE_MODEL)
    result = await scorer.score(output=SUCCESS_OUTPUT, source_text="本文テキスト")
    assert result["score"] == 0.4
    assert result["passed"] is False


async def test_slide_quality_scorer_keeps_prompt_and_model_as_attributes():
    scorer = SlideQualityScorer(judge_model=JUDGE_MODEL)
    assert scorer.judge_model == JUDGE_MODEL
    assert scorer.threshold == 0.5
    assert "{slide_text}" in scorer.prompt
    assert "{source_text}" in scorer.prompt
    # 旧metrics.pyのevaluation_stepsを引き継いだ具体基準を含むこと
    assert "文字の壁" in scorer.prompt


async def test_slide_quality_scorer_propagates_judge_error(monkeypatch):
    async def failing_acompletion(**kwargs):
        raise RuntimeError("judge API error")

    monkeypatch.setattr(scorers_module.litellm, "acompletion", failing_acompletion)
    scorer = SlideQualityScorer(judge_model=JUDGE_MODEL)
    with pytest.raises(RuntimeError, match="judge API error"):
        await scorer.score(output=SUCCESS_OUTPUT, source_text="本文テキスト")


async def test_slide_quality_scorer_skips_judge_without_slide_text(monkeypatch):
    async def failing_acompletion(**kwargs):
        raise AssertionError("judgeを呼んではいけない")

    monkeypatch.setattr(scorers_module.litellm, "acompletion", failing_acompletion)
    scorer = SlideQualityScorer(judge_model=JUDGE_MODEL)
    result = await scorer.score(output=FAILED_OUTPUT, source_text="本文テキスト")
    assert result["passed"] is False


# ---------------------------------------------------------------------------
# run_agent_process
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "generation_success": True,
    "slides": {"title": "t", "slides": []},
    "slide_text": "# t",
    "tool_calls": [],
    "final_text": "done",
    "duration_ms": 123,
    "conversation_id": "baseline:abc",
}


class FakeProcess:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.killed = False

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _install_fake_subprocess(monkeypatch, tmp_path, *, returncode=0, on_start=None):
    """subprocessをfake化し、起動引数からrun workspaceを組み立てる。"""
    monkeypatch.setattr(agent_model, "WORKSPACES_DIR", tmp_path)
    started = {}

    async def fake_exec(*argv, cwd=None):
        args = {argv[i]: argv[i + 1] for i in range(2, len(argv) - 1, 2)}
        started.update(args)
        if on_start is not None:
            on_start(tmp_path, args)
        return FakeProcess(returncode=returncode)

    monkeypatch.setattr(
        agent_model.asyncio, "create_subprocess_exec", fake_exec
    )
    return started


def _write_result(tmp_path, args, payload, *, timestamp="20260812000000"):
    workspace = (
        tmp_path / f"{timestamp}-{args['--variant']}-{args['--run-id']}"
    )
    workspace.mkdir(parents=True)
    (workspace / "evaluation-result.json").write_text(
        json.dumps(payload, ensure_ascii=False)
    )


async def test_run_agent_process_reads_result(monkeypatch, tmp_path):
    _install_fake_subprocess(
        monkeypatch,
        tmp_path,
        on_start=lambda base, args: _write_result(base, args, VALID_RESULT),
    )
    result = await run_agent_process(
        variant="baseline",
        arxiv_id="1706.03762",
        paper_url="https://arxiv.org/abs/1706.03762",
    )
    assert result == VALID_RESULT


async def test_run_agent_process_raises_on_nonzero_exit(monkeypatch, tmp_path):
    _install_fake_subprocess(monkeypatch, tmp_path, returncode=1)
    with pytest.raises(RuntimeError, match="exit code 1"):
        await run_agent_process(
            variant="baseline", arxiv_id="x", paper_url="https://example.com"
        )


async def test_run_agent_process_raises_when_workspace_missing(
    monkeypatch, tmp_path
):
    _install_fake_subprocess(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="0件"):
        await run_agent_process(
            variant="baseline", arxiv_id="x", paper_url="https://example.com"
        )


async def test_run_agent_process_raises_on_multiple_workspaces(
    monkeypatch, tmp_path
):
    def create_two(base, args):
        _write_result(base, args, VALID_RESULT)
        _write_result(base, args, VALID_RESULT, timestamp="20260812000001")

    _install_fake_subprocess(monkeypatch, tmp_path, on_start=create_two)
    with pytest.raises(RuntimeError, match="2件"):
        await run_agent_process(
            variant="baseline", arxiv_id="x", paper_url="https://example.com"
        )


async def test_run_agent_process_raises_on_missing_keys(monkeypatch, tmp_path):
    broken = {key: VALID_RESULT[key] for key in VALID_RESULT if key != "slide_text"}
    _install_fake_subprocess(
        monkeypatch,
        tmp_path,
        on_start=lambda base, args: _write_result(base, args, broken),
    )
    with pytest.raises(RuntimeError, match="slide_text"):
        await run_agent_process(
            variant="baseline", arxiv_id="x", paper_url="https://example.com"
        )


# ---------------------------------------------------------------------------
# build_dataset_rows
# ---------------------------------------------------------------------------


def test_build_dataset_rows_shape(monkeypatch):
    monkeypatch.setattr(
        dataset_module, "fetch_paper_text", lambda arxiv_id: f"本文({arxiv_id})"
    )
    rows = build_dataset_rows()
    assert [row["arxiv_id"] for row in rows] == dataset_module.PAPER_IDS
    for row in rows:
        assert set(row) == {"arxiv_id", "paper_url", "source_text", "expected_tools"}
        assert row["paper_url"] == f"https://arxiv.org/abs/{row['arxiv_id']}"
        assert row["source_text"] == f"本文({row['arxiv_id']})"
        assert row["expected_tools"] == ["execute", "generate_pptx"]

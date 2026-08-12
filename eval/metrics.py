"""Factories for the DeepEval metrics exposed as Weave scorers."""

import hashlib
import json
import os

from deepeval.metrics import GEval, SummarizationMetric, ToolCorrectnessMetric
from deepeval.models import OpenRouterModel
from deepeval.test_case import SingleTurnParams

JUDGE_MODEL = "openai/gpt-5.4"

ASSESSMENT_QUESTIONS = [
    "この文章は、論文が解決しようとする課題や既存手法の限界を述べているか?",
    "この文章は、提案手法の中核的な仕組みを説明しているか?",
    "この文章は、主要な実験結果を具体的な数値とともに示しているか?",
    "この文章は、実験の規模・データセット・計算資源など再現に関わる情報を含んでいるか?",
    "この文章は、論文の貢献または結論を述べているか?",
]

SLIDE_QUALITY_STEPS = [
    "actual_outputは論文から生成されたプレゼンスライドである。各スライドが1つのメッセージに絞られているかを確認し、1枚に複数の論点が詰め込まれていれば減点する",
    "箇条書きの密度を確認する。1スライドに5項目以上ある場合や、1項目が60字を超えて長い場合は「文字の壁」として減点する",
    "スライドタイトルを確認する。「提案手法」「実験結果」のような章名だけのタイトルは減点し、そのスライドの主張を含む具体的なタイトルは加点する",
    "全体が背景と課題→提案手法→結果→結論の論理的な流れで並んでいるかを確認する",
    "省略自体は要約として許容する。内容の欠落よりも、詰め込み・冗長・曖昧なタイトルを重く扱う",
]


def scorer_config_hash() -> str:
    payload = {
        "judge_model": JUDGE_MODEL,
        "assessment_questions": ASSESSMENT_QUESTIONS,
        "slide_quality_steps": SLIDE_QUALITY_STEPS,
        "threshold": 0.5,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_judge_model() -> OpenRouterModel:
    return OpenRouterModel(
        model=JUDGE_MODEL,
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
    )


def build_tool_correctness_metric() -> ToolCorrectnessMetric:
    return ToolCorrectnessMetric(model=build_judge_model())


def build_summarization_metric() -> SummarizationMetric:
    return SummarizationMetric(
        threshold=0.5,
        model=build_judge_model(),
        assessment_questions=ASSESSMENT_QUESTIONS,
    )


def build_slide_quality_metric() -> GEval:
    return GEval(
        name="SlideQuality",
        evaluation_steps=SLIDE_QUALITY_STEPS,
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        threshold=0.5,
        model=build_judge_model(),
    )


def build_metrics() -> list:
    """Compatibility helper for callers that still need all metrics at once."""
    return [
        build_tool_correctness_metric(),
        build_summarization_metric(),
        build_slide_quality_metric(),
    ]

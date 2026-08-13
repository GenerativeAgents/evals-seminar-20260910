"""Weave scorer実装。4つの品質軸を採点する。

- tool_correctness: 自作function-based scorer(決定的判定)
- summarization: プリセットSummarizationScorerへの移譲wrapper
- hallucination_free: プリセットHallucinationFreeScorerへの移譲wrapper
- slide_quality: 自作class-based scorer(weave.Scorer)

LLM-as-a-judgeはlitellm経由で呼び、`openrouter/<provider>/<model>`形式の
model_idでOpenRouterを使う。認証は.envのOPENROUTER_API_KEYをlitellmが読む。
judge APIエラーは0点へ変換せず、そのままscorer errorとして伝播させる。
"""

import json

import litellm
import weave
from weave.scorers import HallucinationFreeScorer, SummarizationScorer

JUDGE_MODEL = "openrouter/openai/gpt-5.4"


def _slide_text_missing_result(output: dict) -> dict | None:
    """採点に必要なslide_textが無い場合、judgeを呼ばず理由付きfailを返す。"""
    if output.get("slide_text"):
        return None
    return {
        "passed": False,
        "failure_reason": output.get("failure_reason")
        or "Model出力にslide_textが含まれていません。",
        "reason": "採点対象のslide_textが無いためfailとします。",
    }


@weave.op
def tool_correctness(output: dict, expected_tools: list[str]) -> dict:
    called = {call["name"] for call in output["tool_calls"]}
    missing = [tool for tool in expected_tools if tool not in called]
    return {
        "passed": not missing,
        "missing_tools": missing,
    }


_summarization = SummarizationScorer(model_id=JUDGE_MODEL)


@weave.op
async def summarization(output: dict, source_text: str) -> dict:
    failed = _slide_text_missing_result(output)
    if failed is not None:
        return failed
    return await _summarization.score(
        input=source_text,
        output=output["slide_text"],
    )


_hallucination_free = HallucinationFreeScorer(model_id=JUDGE_MODEL)


@weave.op
async def hallucination_free(output: dict, source_text: str) -> dict:
    failed = _slide_text_missing_result(output)
    if failed is not None:
        return failed
    return await _hallucination_free.score(
        context=source_text,
        output=output["slide_text"],
    )


SLIDE_QUALITY_PROMPT = """\
あなたはプレゼン資料のレビュアーです。次のスライドを以下の基準で採点してください。

- 1枚のスライドに情報を詰め込みすぎていないか
- 各スライドのタイトルが、そのスライドの主張を一文で表しているか
- スライド全体の流れが論理的か

1(悪い)から5(良い)の整数の"score"と、判定理由の"reason"をJSONで返してください。

# スライド
{slide_text}
"""


class SlideQualityScorer(weave.Scorer):
    judge_model: str
    prompt: str = SLIDE_QUALITY_PROMPT

    @weave.op
    async def score(self, output: dict) -> dict:
        failed = _slide_text_missing_result(output)
        if failed is not None:
            return failed
        response = await litellm.acompletion(
            model=self.judge_model,
            messages=[
                {
                    "role": "user",
                    "content": self.prompt.format(slide_text=output["slide_text"]),
                }
            ],
            response_format={"type": "json_object"},
        )
        verdict = json.loads(response.choices[0].message.content)
        return {"score": verdict["score"], "reason": verdict["reason"]}


def build_scorers() -> list:
    return [
        tool_correctness,
        summarization,
        hallucination_free,
        SlideQualityScorer(judge_model=JUDGE_MODEL),
    ]

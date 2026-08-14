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


# 旧eval/metrics.pyのG-Eval evaluation_stepsをそのまま採点基準として使い、
# DeepEvalのG-Evalテンプレート構造(steps + input + actual_output → 0〜10の
# 整数score)に合わせる。logprobsによるスコア加重は行わない。
SLIDE_QUALITY_PROMPT = """\
あなたは評価者です。以下のEvaluation Stepsに基づいてActual Outputを採点してください。

# Evaluation Steps
- actual_outputは論文から生成されたプレゼンスライドである。各スライドが1つのメッセージに絞られているかを確認し、1枚に複数の論点が詰め込まれていれば減点する
- 箇条書きの密度を確認する。1スライドに5項目以上ある場合や、1項目が60字を超えて長い場合は「文字の壁」として減点する
- スライドタイトルを確認する。「提案手法」「実験結果」のような章名だけのタイトルは減点し、そのスライドの主張を含む具体的なタイトルは加点する
- 全体が背景と課題→提案手法→結果→結論の論理的な流れで並んでいるかを確認する
- 省略自体は要約として許容する。内容の欠落よりも、詰め込み・冗長・曖昧なタイトルを重く扱う

# Input(論文本文)
{source_text}

# Actual Output(スライド)
{slide_text}

Evaluation Stepsに基づき、次の2つのキーを持つJSONだけを返してください。
- "score": 0から10の整数。10はEvaluation Stepsの基準を完全に満たすことを、0はまったく満たさないことを意味する
- "reason": 採点理由。Actual Outputの具体的な箇所に言及し、scoreの数値自体は引用しない
"""


class SlideQualityScorer(weave.Scorer):
    judge_model: str
    prompt: str = SLIDE_QUALITY_PROMPT
    threshold: float = 0.5

    @weave.op
    async def score(self, output: dict, source_text: str) -> dict:
        failed = _slide_text_missing_result(output)
        if failed is not None:
            return failed
        response = await litellm.acompletion(
            model=self.judge_model,
            messages=[
                {
                    "role": "user",
                    "content": self.prompt.format(
                        source_text=source_text,
                        slide_text=output["slide_text"],
                    ),
                }
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        verdict = json.loads(response.choices[0].message.content)
        # 旧DeepEval G-Evalと同じ0〜1尺度へ正規化する
        score = int(verdict["score"]) / 10
        return {
            "score": score,
            "passed": score >= self.threshold,
            "reason": verdict["reason"],
        }


def build_scorers() -> list:
    return [
        tool_correctness,
        summarization,
        hallucination_free,
        SlideQualityScorer(judge_model=JUDGE_MODEL),
    ]

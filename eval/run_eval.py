"""publish済みDatasetに対して、指定variantのエージェントをライブ評価する。

使い方:
    uv run eval/run_eval.py baseline
    uv run eval/run_eval.py improvement-1
    uv run eval/run_eval.py improvement-2

Evaluationが作るCall IDを別processのTypeScript Agentへ渡し、Agent Traceを
各evaluation resultへ紐付ける。各Dataset行の実行は1回で、反復回数のオプションは
無い。評価結果の正本はWeaveであり、results/<variant>/は評価入力として読まない。
"""

import argparse
import asyncio
from typing import Any
from time import perf_counter
from types import SimpleNamespace

from pydantic import PrivateAttr

import weave
from agent_model import SlideAgentModel
from dataset import DATASET_NAME, VARIANTS, load_settings
from scorers import build_scorers
from weave.flow.scorer import get_scorer_attributes


def build_eval_context(
    prediction: Any, *, example_id: str, evaluation_name: str
) -> dict[str, str | int]:
    """別processのAgent spanへ付けるEvaluation link属性を作る。"""
    return {
        "weave.eval.run_id": prediction.evaluate_call.id,
        "weave.eval.predict_and_score_call_id": (
            prediction.predict_and_score_call.id
        ),
        "weave.eval.kind": "agent",
        "weave.eval.example_id": example_id,
        "weave.eval.trial_index": 0,
        "weave.eval.evaluation_name": evaluation_name,
    }


class SlideEvaluation(weave.Evaluation):
    """Dataset/scorer参照を保存し、採点失敗時も他のscorerを実行する。"""

    _errors: int = PrivateAttr(default=0)

    @weave.op(name="Evaluation.predict_and_score")
    async def predict_and_score(self, model: Any, example: dict) -> dict:
        row_call = weave.require_current_call()
        context = build_eval_context(
            SimpleNamespace(
                evaluate_call=SimpleNamespace(id=row_call.parent_id),
                predict_and_score_call=row_call,
            ),
            example_id=str(example["arxiv_id"]),
            evaluation_name=self.name,
        )
        started = perf_counter()
        try:
            output, model_call = await model.predict.call(
                model,
                arxiv_id=example["arxiv_id"],
                paper_url=example["paper_url"],
                eval_context=context,
            )
        except Exception:
            self._errors += 1
            raise
        latency = perf_counter() - started
        scores = {}
        for scorer in self.scorers or []:
            scorer_name = get_scorer_attributes(scorer).scorer_name
            try:
                # 標準APIでmodel callへのscore feedbackも保存する。
                result = await model_call.apply_scorer(scorer, example)
                scores[scorer_name] = result.result
            except Exception as error:
                self._errors += 1
                print(
                    f"[scorer error] example={example['arxiv_id']} "
                    f"scorer={scorer_name}: {error}"
                )
        return {"output": output, "scores": scores, "model_latency": latency}


async def run_evaluation(*, variant: str, dataset: Any) -> tuple[str | None, int]:
    """標準EvaluationへDatasetと実scorerを渡し、行別scoreと集計を保存する。"""
    model = SlideAgentModel(variant=variant)
    evaluation = SlideEvaluation(
        name=variant,
        evaluation_name=variant,
        dataset=dataset,
        scorers=build_scorers(),
        trials=1,
    )
    _, call = await evaluation.evaluate.call(evaluation, model)
    return call.ui_url, evaluation._errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("variant", choices=VARIANTS)
    args = parser.parse_args()

    settings = load_settings(require_openrouter=True)
    client = weave.init(settings.weave_project)

    # 設定形式にかかわらずrefを解決できるよう、initが解決した値を使う
    dataset_uri = (
        f"weave:///{client.entity}/{client.project}/object/{DATASET_NAME}:latest"
    )
    try:
        dataset = weave.ref(dataset_uri).get()
    except Exception as error:
        raise SystemExit(
            f"Datasetを取得できませんでした: {dataset_uri}\n"
            "先に `uv run eval/publish_dataset.py` で自分のprojectへ"
            f"Datasetをpublishしてください。\n詳細: {error}"
        ) from error

    evaluation_url, errors = asyncio.run(
        run_evaluation(variant=args.variant, dataset=dataset)
    )

    print(f"[dataset] {dataset.ref.uri() if dataset.ref else dataset_uri}")
    print(f"[model] variant={args.variant}")
    if evaluation_url:
        print(f"[evaluation] {evaluation_url}")
    print(f"[errors] {errors}")


if __name__ == "__main__":
    main()

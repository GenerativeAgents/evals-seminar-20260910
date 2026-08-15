"""publish済みDatasetに対して、指定variantのエージェントをライブ評価する。

使い方:
    uv run eval/run_eval.py baseline
    uv run eval/run_eval.py improvement-1
    uv run eval/run_eval.py improvement-2

EvaluationLoggerが作るCall IDを別processのTypeScript Agentへ渡し、Agent Traceを
各evaluation resultへ紐付ける。各Dataset行の実行は1回で、反復回数のオプションは
無い。評価結果の正本はWeaveであり、results/<variant>/は評価入力として読まない。
"""

import argparse
import asyncio
from typing import Any

import weave
from agent_model import SlideAgentModel
from dataset import DATASET_NAME, VARIANTS, load_settings
from scorers import build_scorers
from weave.flow.scorer import apply_scorer_async, get_scorer_attributes


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


async def apply_and_log_scorer(
    *, prediction: Any, scorer: Any, example: dict[str, Any], output: dict
) -> None:
    """既存scorerをそのまま実行し、EvaluationLoggerへ結果を記録する。"""
    scorer_attributes = get_scorer_attributes(scorer)

    # log_score contextの内側で実行することで、judge LLMのusage/costを
    # predict costではなく該当scorerへ帰属させる。
    with prediction.log_score(scorer_attributes.scorer_name) as logged_score:
        # Weave標準の実行経路を使い、function-based scorerの引数解決に加えて
        # class-based scorerへインスタンス(self)を正しくbindする。
        result = await apply_scorer_async(scorer, example, output)
        logged_score.value = result.result


async def run_evaluation(*, variant: str, dataset: Any) -> tuple[str | None, int]:
    """EvaluationLoggerを使い、Dataset全行を1 trialずつ評価する。"""
    model = SlideAgentModel(variant=variant)
    scorers = build_scorers()
    scorer_names = [
        get_scorer_attributes(scorer).scorer_name for scorer in scorers
    ]
    eval_logger = weave.EvaluationLogger(
        name=variant,
        model=model,
        dataset=dataset,
        scorers=scorer_names,
    )
    errors = 0

    try:
        for row in dataset.rows:
            # published Datasetのrow objectをそのまま渡すことでrow refを維持する。
            example = row
            example_id = str(example["arxiv_id"])
            try:
                with eval_logger.log_prediction(
                    inputs=example,
                    example_id=example_id,
                    trial_index=0,
                ) as prediction:
                    eval_context = build_eval_context(
                        prediction,
                        example_id=example_id,
                        evaluation_name=variant,
                    )
                    output = await model.predict(
                        arxiv_id=example["arxiv_id"],
                        paper_url=example["paper_url"],
                        eval_context=eval_context,
                    )
                    prediction.output = output

                    for scorer in scorers:
                        try:
                            await apply_and_log_scorer(
                                prediction=prediction,
                                scorer=scorer,
                                example=example,
                                output=output,
                            )
                        except Exception as error:
                            errors += 1
                            scorer_name = get_scorer_attributes(scorer).scorer_name
                            print(
                                f"[scorer error] example={example_id} "
                                f"scorer={scorer_name}: {error}"
                            )
            except Exception as error:
                errors += 1
                print(f"[prediction error] example={example_id}: {error}")
    except BaseException as error:
        eval_logger.fail(error)
        raise
    else:
        eval_logger.log_summary()

    return eval_logger.ui_url, errors


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

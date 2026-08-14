"""
publish済みのWeave Datasetに対して、指定variantのエージェントをライブ評価する。

使い方:
    uv run eval/run_eval.py baseline
    uv run eval/run_eval.py improvement-1
    uv run eval/run_eval.py improvement-2

各Dataset行の実行は1回で、反復回数のオプションは無い。
評価結果の正本はWeaveであり、results/<variant>/は評価入力として読まない。
"""

import argparse
import asyncio
import json

import weave
from agent_model import SlideAgentModel
from dataset import DATASET_NAME, VARIANTS, load_settings
from scorers import build_scorers


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

    evaluation = weave.Evaluation(
        evaluation_name=DATASET_NAME,
        dataset=dataset,
        scorers=build_scorers(),
    )
    model = SlideAgentModel(variant=args.variant)
    summary = asyncio.run(
        evaluation.evaluate(model, __weave={"display_name": args.variant})
    )

    print(f"[dataset] {dataset.ref.uri() if dataset.ref else dataset_uri}")
    print(f"[model] variant={args.variant}")
    print("[summary]")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

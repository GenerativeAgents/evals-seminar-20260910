"""Run live slide-agent evaluations with W&B Weave."""

import argparse
import asyncio
import importlib.metadata
import os
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

# DeepEval's asyncio timeout cancels traced judge calls without giving Weave a
# matching call end, which makes process shutdown wait for the 300-second flush
# timeout. Provider SDK request timeouts remain enabled.
os.environ["DEEPEVAL_DISABLE_TIMEOUTS"] = "true"

import weave  # noqa: E402
from agent_model import SlideAgentModel  # noqa: E402
from cases import build_slide_test_case  # noqa: E402
from metrics import (  # noqa: E402
    JUDGE_MODEL,
    build_slide_quality_metric,
    build_summarization_metric,
    build_tool_correctness_metric,
    scorer_config_hash,
)
from weave_scorer import deepeval_metric_to_weave_scorer  # noqa: E402

VARIANTS = ("baseline", "improvement-1", "improvement-2")
REQUIRED_COLUMNS = {"arxiv_id", "paper_url", "source_text", "expected_tools"}


def configured(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("your_"):
        raise RuntimeError(f"{name} is not configured.")
    return value


def dataset_ref_uri(dataset: Any) -> str:
    ref = getattr(dataset, "ref", None)
    if ref is None:
        raise RuntimeError("The retrieved Dataset does not have a Weave ref.")
    uri = getattr(ref, "uri", None)
    return uri() if callable(uri) else str(ref)


def dataset_rows(dataset: Any) -> list[dict]:
    rows = [dict(row) for row in dataset.rows]
    if not rows:
        raise RuntimeError("The Weave Dataset is empty.")
    for index, row in enumerate(rows):
        missing = REQUIRED_COLUMNS - row.keys()
        if missing:
            raise RuntimeError(f"Dataset row {index} is missing: {sorted(missing)}")
    return rows


def make_scorers() -> list:
    return [
        deepeval_metric_to_weave_scorer(
            name="tool_correctness",
            metric_factory=build_tool_correctness_metric,
            test_case_factory=build_slide_test_case,
        ),
        deepeval_metric_to_weave_scorer(
            name="summarization",
            metric_factory=build_summarization_metric,
            test_case_factory=build_slide_test_case,
        ),
        deepeval_metric_to_weave_scorer(
            name="slide_quality",
            metric_factory=build_slide_quality_metric,
            test_case_factory=build_slide_test_case,
        ),
    ]


def select_evaluation_dataset(
    dataset: Any, rows: list[dict], arxiv_id: str | None
) -> tuple[Any, str]:
    if arxiv_id is None:
        return dataset, "full"
    selected = [row for row in rows if row["arxiv_id"] == arxiv_id]
    if not selected:
        raise RuntimeError(f"arXiv ID is not present in the Dataset: {arxiv_id}")
    return weave.Dataset(rows=selected), "smoke"


async def run(args: argparse.Namespace) -> dict:
    configured("WANDB_API_KEY")
    configured("OPENROUTER_API_KEY")
    project = configured("WEAVE_PROJECT")
    if args.trials < 1:
        raise RuntimeError("--trials must be at least 1.")
    if args.agent_timeout_seconds < 1:
        raise RuntimeError("--agent-timeout-seconds must be at least 1.")

    weave.init(project)
    dataset = weave.ref(args.dataset).get()
    resolved_ref = dataset_ref_uri(dataset)
    rows = dataset_rows(dataset)
    evaluation_dataset, scope = select_evaluation_dataset(
        dataset, rows, args.arxiv_id
    )
    metadata = {
        "scope": scope,
        "source_dataset_ref": resolved_ref,
        "arxiv_id": args.arxiv_id,
        "trials": args.trials,
        "judge_model": JUDGE_MODEL,
        "scorer_config_hash": scorer_config_hash(),
        "deepeval_version": importlib.metadata.version("deepeval"),
    }
    evaluation = weave.Evaluation(
        evaluation_name="evals-seminar-20260910",
        dataset=evaluation_dataset,
        scorers=make_scorers(),
        trials=args.trials,
        metadata=metadata,
    )
    model = SlideAgentModel(
        variant=args.variant, timeout_seconds=args.agent_timeout_seconds
    )
    print(
        f"[info] variant={args.variant} scope={scope} trials={args.trials} "
        f"dataset={resolved_ref}"
    )
    evaluation_call_id = str(uuid.uuid4())
    result = await evaluation.evaluate(
        model,
        __weave={
            "display_name": f"{args.variant}-{scope}",
            "call_id": evaluation_call_id,
        },
    )
    print(f"[result] {result}")
    print(f"[evaluation_call] {evaluation_call_id}")
    ref = getattr(evaluation, "ref", None)
    if ref is not None:
        ui_url = getattr(ref, "ui_url", None)
        print(f"[evaluation] {ui_url() if callable(ui_url) else ref}")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("variant", choices=VARIANTS)
    parser.add_argument(
        "--dataset",
        default=os.environ.get("WEAVE_DATASET", "evals-seminar-20260910"),
    )
    parser.add_argument("--arxiv-id", help="Evaluate one Dataset row as a smoke run")
    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Weave Evaluation trials per Dataset row",
    )
    parser.add_argument("--agent-timeout-seconds", type=int, default=900)
    return parser.parse_args()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()

"""Publish the fixed three-paper Dataset used by live Weave evaluations."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

import weave  # noqa: E402
from cases import build_dataset_rows  # noqa: E402


def configured(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("your_"):
        raise RuntimeError(f"{name} is not configured.")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default=os.environ.get("WEAVE_DATASET", "evals-seminar-20260910"),
    )
    args = parser.parse_args()
    configured("WANDB_API_KEY")
    project = configured("WEAVE_PROJECT")
    weave.init(project)
    rows = build_dataset_rows()
    dataset = weave.Dataset(name=args.dataset, rows=rows)
    dataset_ref = weave.publish(dataset)
    print(f"[published] name={args.dataset} rows={len(rows)}")
    print(f"[dataset] {dataset_ref.uri()}")


if __name__ == "__main__":
    main()

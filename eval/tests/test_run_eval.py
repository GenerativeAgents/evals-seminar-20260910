import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_eval import dataset_rows, select_evaluation_dataset  # noqa: E402
from deepeval.models.retry_policy import (  # noqa: E402
    resolve_effective_attempt_timeout,
)


class FakeDataset:
    def __init__(self, rows):
        self.rows = rows


class RunEvalTest(unittest.TestCase):
    def setUp(self):
        self.row = {
            "arxiv_id": "1706.03762",
            "paper_url": "https://arxiv.org/abs/1706.03762",
            "source_text": "source",
            "expected_tools": ["execute", "generate_pptx"],
        }

    def test_full_scope_preserves_published_dataset(self):
        dataset = FakeDataset([self.row])
        rows = dataset_rows(dataset)
        selected, scope = select_evaluation_dataset(dataset, rows, None)
        self.assertIs(selected, dataset)
        self.assertEqual(scope, "full")

    def test_disables_deepeval_cancelling_traced_judge_calls(self):
        self.assertEqual(os.environ["DEEPEVAL_DISABLE_TIMEOUTS"], "true")
        self.assertEqual(resolve_effective_attempt_timeout(), 0)

    def test_smoke_scope_selects_exact_arxiv_id(self):
        dataset = FakeDataset([self.row])
        selected, scope = select_evaluation_dataset(
            dataset, dataset_rows(dataset), "1706.03762"
        )
        self.assertEqual(scope, "smoke")
        self.assertEqual(list(selected.rows)[0]["arxiv_id"], "1706.03762")


if __name__ == "__main__":
    unittest.main()

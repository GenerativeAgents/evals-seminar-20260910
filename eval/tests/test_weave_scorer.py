import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weave_scorer import deepeval_metric_to_weave_scorer  # noqa: E402


class FakeMetric:
    async_mode = True
    score = None
    reason = None
    error = None
    score_breakdown = None
    evaluation_cost = None
    input_tokens = None
    output_tokens = None

    async def a_measure(self, _case):
        self.score = 0.75
        self.reason = "measured"
        return self.score

    def is_successful(self):
        return True


class ScorerTest(unittest.IsolatedAsyncioTestCase):
    async def test_creates_a_fresh_metric_and_returns_details(self):
        instances = []

        def factory():
            metric = FakeMetric()
            instances.append(metric)
            return metric

        scorer = deepeval_metric_to_weave_scorer(
            name="fake_metric",
            metric_factory=factory,
            test_case_factory=lambda **_kwargs: object(),
        )
        output = {
            "generation_success": True,
            "slides_text": "slides",
            "tool_calls": [],
        }
        first = await scorer(output=output, source_text="source", expected_tools=[])
        second = await scorer(output=output, source_text="source", expected_tools=[])
        self.assertEqual(len(instances), 2)
        self.assertEqual(first, {"score": 0.75, "passed": True, "reason": "measured"})
        self.assertEqual(second["score"], 0.75)

    async def test_structured_generation_failure_is_zero_not_an_exception(self):
        scorer = deepeval_metric_to_weave_scorer(
            name="fake_failure",
            metric_factory=lambda: FakeMetric(),
            test_case_factory=lambda **_kwargs: object(),
        )
        result = await scorer(
            output={
                "generation_success": False,
                "slides_text": "",
                "error": {"message": "generate_pptx was not called"},
            },
            source_text="source",
            expected_tools=[],
        )
        self.assertEqual(result["score"], 0.0)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "generate_pptx was not called")


if __name__ == "__main__":
    unittest.main()

"""Adapt single-turn DeepEval BaseMetric factories to Weave scorers."""

import asyncio
from collections.abc import Callable

import weave
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

MetricFactory = Callable[[], BaseMetric]
TestCaseFactory = Callable[..., LLMTestCase]


def deepeval_metric_to_weave_scorer(
    *,
    name: str,
    metric_factory: MetricFactory,
    test_case_factory: TestCaseFactory,
):
    """Convert a single-turn LLMTestCase metric (not conversational/arena)."""

    @weave.op(name=name)
    async def scorer(output: dict, source_text: str, expected_tools: list[str]) -> dict:
        if not output.get("generation_success") or not output.get("slides_text"):
            error = output.get("error") or {}
            reason = error.get("message") if isinstance(error, dict) else None
            return {
                "score": 0.0,
                "passed": False,
                "reason": reason or "The agent did not produce evaluable slides.",
            }

        metric = metric_factory()
        test_case = test_case_factory(
            output=output,
            source_text=source_text,
            expected_tools=expected_tools,
        )
        if metric.async_mode:
            measured_score = await metric.a_measure(test_case)
        else:
            measured_score = await asyncio.to_thread(metric.measure, test_case)
        if metric.error:
            raise RuntimeError(str(metric.error))

        score = metric.score if metric.score is not None else measured_score
        result = {
            "score": float(score),
            "passed": bool(metric.is_successful()),
        }
        optional_fields = {
            "reason": metric.reason,
            "score_breakdown": metric.score_breakdown,
            "evaluation_cost": metric.evaluation_cost,
            "input_tokens": metric.input_tokens,
            "output_tokens": metric.output_tokens,
        }
        result.update(
            {key: value for key, value in optional_fields.items() if value is not None}
        )
        return result

    return scorer

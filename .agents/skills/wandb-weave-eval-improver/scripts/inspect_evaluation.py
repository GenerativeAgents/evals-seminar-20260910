#!/usr/bin/env python3
"""Inspect one Weave Evaluation root call with bounded row and trace reads."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlparse


CALL_URL_PATTERN = re.compile(
    r"^/(?P<entity>[^/]+)/(?P<project>[^/]+)/r/call/(?P<call_id>[^/?#]+)"
)
MAX_STRING_LENGTH = 600
MAX_CONTAINER_ITEMS = 50
MAX_CONTRACT_TRACE_CALLS = 500
KNOWN_FUNCTION_SCORERS = {
    "tool_correctness",
    "summarization",
    "hallucination_free",
}


class InspectionError(RuntimeError):
    """Raised when an Evaluation cannot be inspected safely."""


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "items"):
        try:
            return {str(key): _plain(item) for key, item in value.items()}
        except Exception:
            pass
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _clip(value: Any) -> Any:
    value = _plain(value)
    if isinstance(value, str):
        if len(value) <= MAX_STRING_LENGTH:
            return value
        return value[:MAX_STRING_LENGTH] + "...[truncated]"
    if isinstance(value, dict):
        items = list(value.items())
        clipped = {
            key: _clip(item) for key, item in items[:MAX_CONTAINER_ITEMS]
        }
        if len(items) > MAX_CONTAINER_ITEMS:
            clipped["...truncated_items"] = len(items) - MAX_CONTAINER_ITEMS
        return clipped
    if isinstance(value, list):
        clipped_list = [_clip(item) for item in value[:MAX_CONTAINER_ITEMS]]
        if len(value) > MAX_CONTAINER_ITEMS:
            clipped_list.append(
                {"...truncated_items": len(value) - MAX_CONTAINER_ITEMS}
            )
        return clipped_list
    return value


def _field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    try:
        return getattr(value, key)
    except (AttributeError, TypeError):
        return default


def _ref_uri(value: Any) -> str | None:
    if isinstance(value, str):
        return value if value.startswith("weave:///") else None
    ref = _field(value, "ref")
    if ref is not None:
        uri = getattr(ref, "uri", None)
        if callable(uri):
            try:
                return str(uri())
            except Exception:
                pass
        ref_string = str(ref)
        if ref_string.startswith("weave:///"):
            return ref_string
    for key in ("_ref", "ref", "uri"):
        candidate = _field(value, key)
        if isinstance(candidate, str) and candidate.startswith("weave:///"):
            return candidate
    return None


def parse_locator(
    locator: str, default_project: str | None
) -> tuple[str, str]:
    locator = locator.strip()
    parsed = urlparse(locator)
    if parsed.scheme and parsed.netloc:
        match = CALL_URL_PATTERN.match(parsed.path)
        if not match:
            raise InspectionError(
                "Expected a W&B call URL ending in /ENTITY/PROJECT/r/call/CALL_ID"
            )
        project = f"{unquote(match['entity'])}/{unquote(match['project'])}"
        return project, unquote(match["call_id"])

    if not default_project or default_project.count("/") != 1:
        raise InspectionError(
            "A bare Evaluation ID requires --project ENTITY/PROJECT or "
            "WANDB_ENTITY and WANDB_PROJECT"
        )
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", locator):
        raise InspectionError("Evaluation ID has an invalid shape")
    return default_project, locator


def _nested(value: Any, *keys: str, default: Any = None) -> Any:
    current = _plain(value)
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _short_op(op_name: str | None) -> str | None:
    if not op_name:
        return None
    return op_name.split("/op/")[-1].rsplit(":", 1)[0]


def _status(call: Any) -> str | None:
    return _nested(getattr(call, "summary", None), "weave", "status")


def _duration_seconds(call: Any) -> float | None:
    started = getattr(call, "started_at", None)
    ended = getattr(call, "ended_at", None)
    if started is None or ended is None:
        return None
    return round((ended - started).total_seconds(), 6)


def _usage(summary: Any) -> dict[str, int]:
    usage = _nested(summary, "usage", default={})
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    if not isinstance(usage, dict):
        return totals
    for model_usage in usage.values():
        if not isinstance(model_usage, dict):
            continue
        totals["input_tokens"] += int(
            model_usage.get("input_tokens")
            or model_usage.get("prompt_tokens")
            or 0
        )
        totals["output_tokens"] += int(
            model_usage.get("output_tokens")
            or model_usage.get("completion_tokens")
            or 0
        )
        totals["total_tokens"] += int(model_usage.get("total_tokens") or 0)
    return totals


def _sum_costs(value: Any, *, parent_key: str = "") -> float:
    value = _plain(value)
    if isinstance(value, dict):
        for key in ("total_cost", "cost"):
            direct = value.get(key)
            if isinstance(direct, (int, float)):
                return float(direct)
        return sum(
            _sum_costs(item, parent_key=str(key).lower())
            for key, item in value.items()
        )
    if isinstance(value, list):
        return sum(_sum_costs(item, parent_key=parent_key) for item in value)
    if isinstance(value, (int, float)) and "cost" in parent_key:
        return float(value)
    return 0.0


def _example_id(inputs: Any) -> str | None:
    candidates: list[Any] = []
    example = _field(inputs, "example", inputs)
    candidates.extend(
        _field(example, key) for key in ("arxiv_id", "example_id", "id")
    )
    for candidate in candidates:
        if candidate is not None and str(candidate):
            return str(candidate)
    return None


def _flatten_score_values(value: Any, prefix: str = "") -> dict[str, Any]:
    value = _plain(value)
    flattened: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(item, dict):
                flattened.update(_flatten_score_values(item, path))
            elif isinstance(item, (bool, int, float)) or item is None:
                flattened[path] = item
            elif str(key) in {"reason", "failure_reason", "missing_tools"}:
                flattened[path] = _clip(item)
    return flattened


def _scorer_contract(calls: Sequence[Any]) -> list[dict[str, Any]]:
    contract: list[dict[str, Any]] = []
    seen: set[str] = set()
    for call in calls:
        op_name = _short_op(getattr(call, "op_name", None))
        if not op_name or not (
            op_name.endswith("_scorer")
            or op_name.endswith(".score")
            or op_name in KNOWN_FUNCTION_SCORERS
        ):
            continue
        inputs = getattr(call, "inputs", None)
        scorer_self = _field(inputs, "self", {})
        prompt = _field(scorer_self, "prompt")
        item = {
            "op_name": op_name,
            "op_ref": getattr(call, "op_name", None),
        }
        for key in ("judge_model", "model_id", "threshold"):
            value = _field(scorer_self, key)
            if isinstance(value, (str, int, float, bool)):
                item[key] = value
        if isinstance(prompt, str):
            item["prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
        identity = json.dumps(item, sort_keys=True, default=str)
        if identity in seen:
            continue
        seen.add(identity)
        contract.append(item)
    return sorted(contract, key=lambda item: (str(item["op_name"]), str(item["op_ref"])))


def _prediction_summary(call: Any) -> dict[str, Any]:
    output = getattr(call, "output", None)
    scores = _field(output, "scores", {})
    model_output = _field(output, "output", {})
    conversation_id = _field(model_output, "conversation_id")
    flattened = _flatten_score_values(scores)
    status = _status(call)
    exception = getattr(call, "exception", None)
    failed_checks = sorted(
        key
        for key, value in flattened.items()
        if key.endswith("passed") and value is False
    )
    return {
        "call_id": getattr(call, "id", None),
        "example_id": _example_id(getattr(call, "inputs", None)),
        "conversation_id": (
            str(conversation_id) if conversation_id is not None else None
        ),
        "status": status,
        "exception": _clip(exception),
        "duration_seconds": _duration_seconds(call),
        "usage": _usage(getattr(call, "summary", None)),
        "cost": round(_sum_costs(getattr(call, "summary", None)), 12),
        "scores": flattened,
        "failed_checks": failed_checks,
        "is_failure": bool(exception)
        or status in {"error", "descendant_error"}
        or bool(failed_checks),
    }


def summarize_evaluation(
    evaluation_call: Any,
    prediction_calls: Sequence[Any],
    *,
    total_predictions: int | None = None,
    linked_traces: Mapping[str, Sequence[Any]] | None = None,
    scorer_calls: Sequence[Any] = (),
) -> dict[str, Any]:
    func_name = getattr(evaluation_call, "func_name", None)
    op_name = getattr(evaluation_call, "op_name", None)
    if func_name != "Evaluation.evaluate" and _short_op(op_name) != "Evaluation.evaluate":
        raise InspectionError("The supplied call is not an Evaluation.evaluate root")

    root_inputs = getattr(evaluation_call, "inputs", None)
    evaluation_object = _field(root_inputs, "self", {})
    metadata = _plain(_field(evaluation_object, "metadata", {}))
    dataset = _field(evaluation_object, "dataset")
    dataset_ref = _ref_uri(dataset)
    model = _field(root_inputs, "model")

    rows = [_prediction_summary(call) for call in prediction_calls]
    linked_traces = linked_traces or {}
    for row in rows:
        trace_calls = linked_traces.get(str(row["call_id"]), [])
        row["linked_agent_traces"] = [
            {
                "call_id": getattr(call, "id", None),
                "trace_id": getattr(call, "trace_id", None),
                "op_name": _short_op(getattr(call, "op_name", None)),
                "status": _status(call),
                "duration_seconds": _duration_seconds(call),
            }
            for call in trace_calls
        ]

    root_summary = getattr(evaluation_call, "summary", None)
    total = total_predictions if total_predictions is not None else len(rows)
    return {
        "evaluation": {
            "id": getattr(evaluation_call, "id", None),
            "url": getattr(evaluation_call, "ui_url", None),
            "display_name": getattr(evaluation_call, "display_name", None),
            "status": _status(evaluation_call),
            "exception": _clip(getattr(evaluation_call, "exception", None)),
            "started_at": _plain(getattr(evaluation_call, "started_at", None)),
            "ended_at": _plain(getattr(evaluation_call, "ended_at", None)),
            "duration_seconds": _duration_seconds(evaluation_call),
            "attributes": _clip(getattr(evaluation_call, "attributes", None)),
            "usage": _usage(root_summary),
            "cost": round(_sum_costs(root_summary), 12),
            "status_counts": _clip(
                _nested(root_summary, "status_counts", default={})
            ),
        },
        "contract": {
            "dataset_ref": dataset_ref,
            "dataset_ref_missing": dataset is not None and dataset_ref is None,
            "scorers": _clip(
                metadata.get("scorers", []) if isinstance(metadata, dict) else []
            ),
            "evaluation_metadata": _clip(metadata),
            "model": _clip(model),
            "scorer_ops": _scorer_contract(scorer_calls),
        },
        "prediction_count": total,
        "returned_prediction_count": len(rows),
        "truncated": len(rows) < total,
        "failure_count_in_returned_rows": sum(row["is_failure"] for row in rows),
        "rows": rows,
    }


def _project_from_environment() -> str | None:
    entity = os.environ.get("WANDB_ENTITY")
    project = os.environ.get("WANDB_PROJECT")
    if entity and project:
        return f"{entity}/{project}"
    legacy = os.environ.get("WEAVE_PROJECT")
    if legacy and legacy.count("/") == 1:
        return legacy
    return None


def inspect_remote(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import weave
        from weave.trace.weave_client import CallsFilter
        from weave.trace_server.interface.query import Query
        from weave.trace_server.trace_server_interface import CallsQueryStatsReq
    except ImportError as error:
        raise InspectionError(
            "Install this repository's Python dependencies before inspection"
        ) from error

    project, call_id = parse_locator(
        args.evaluation, args.project or _project_from_environment()
    )
    logging.getLogger("weave").setLevel(logging.ERROR)
    client = weave.init(project)
    evaluation_call = client.get_call(call_id, include_costs=True)
    entity, project_name = project.split("/", 1)
    op_ref = f"weave:///{entity}/{project_name}/op/Evaluation.predict_and_score:*"
    call_filter = CallsFilter(op_names=[op_ref], parent_ids=[call_id])
    stats = client.server.calls_query_stats(
        CallsQueryStatsReq(
            project_id=project,
            filter=call_filter.model_dump(exclude_none=True),
        )
    )
    total = stats.count
    predictions = (
        list(
            client.get_calls(
                filter=call_filter,
                limit=min(args.max_rows, total),
                include_costs=True,
                columns=[
                    "id",
                    "op_name",
                    "display_name",
                    "started_at",
                    "ended_at",
                    "inputs",
                    "output",
                    "exception",
                    "summary",
                    "attributes",
                ],
            )
        )
        if total
        else []
    )

    linked: dict[str, list[Any]] = {}
    scorer_calls: list[Any] = []
    contract_trace_count = 0
    trace_id = getattr(evaluation_call, "trace_id", None)
    if trace_id:
        contract_filter = CallsFilter(trace_ids=[trace_id])
        contract_trace_count = client.server.calls_query_stats(
            CallsQueryStatsReq(
                project_id=project,
                filter=contract_filter.model_dump(exclude_none=True),
            )
        ).count
        scorer_calls = list(
            client.get_calls(
                filter=contract_filter,
                limit=min(MAX_CONTRACT_TRACE_CALLS, contract_trace_count),
                columns=["op_name", "inputs"],
            )
        ) if contract_trace_count else []
    if args.include_agent_traces:
        for prediction in predictions:
            prediction_summary = _prediction_summary(prediction)
            if not prediction_summary["is_failure"]:
                continue
            query = Query(
                **{
                    "$expr": {
                        "$eq": [
                            {
                                "$getField": "attributes.weave.eval.predict_and_score_call_id"
                            },
                            {"$literal": prediction.id},
                        ]
                    }
                }
            )
            linked[prediction.id] = list(
                client.get_calls(
                    filter=CallsFilter(trace_roots_only=True),
                    query=query,
                    limit=args.max_traces_per_row,
                    columns=[
                        "id",
                        "trace_id",
                        "op_name",
                        "started_at",
                        "ended_at",
                        "summary",
                    ],
                )
            )

    result = summarize_evaluation(
        evaluation_call,
        predictions,
        total_predictions=total,
        linked_traces=linked,
        scorer_calls=scorer_calls,
    )
    result["project"] = project
    result["query"] = {
        "max_rows": args.max_rows,
        "include_agent_traces": args.include_agent_traces,
        "max_traces_per_row": args.max_traces_per_row,
        "contract_trace_call_count": contract_trace_count,
        "contract_trace_truncated": contract_trace_count > MAX_CONTRACT_TRACE_CALLS,
    }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluation", help="Evaluation root Call ID or W&B call URL")
    parser.add_argument("--project", help="ENTITY/PROJECT for a bare ID")
    parser.add_argument("--max-rows", type=int, default=100)
    parser.add_argument("--include-agent-traces", action="store_true")
    parser.add_argument("--max-traces-per-row", type=int, default=5)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.max_rows <= 500:
        parser.error("--max-rows must be between 1 and 500")
    if not 1 <= args.max_traces_per_row <= 20:
        parser.error("--max-traces-per-row must be between 1 and 20")
    try:
        result = inspect_remote(args)
    except InspectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

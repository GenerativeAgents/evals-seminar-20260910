#!/usr/bin/env python3
"""Create and track one isolated Evaluation-improvement Git worktree.

This utility deliberately has no delete, merge, reset, stash, or checkout
operation. State and worktrees are retained for inspection.
"""

from __future__ import annotations

import argparse
import ast
import copy
import fcntl
import hashlib
import json
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.parse import urlparse


TRIAL_PATTERN = re.compile(r"^eval-[a-z0-9]{6,12}-\d{14}$")
EVALUATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
ALWAYS_PROTECTED_PREFIXES = (
    "eval/dataset.py",
    "eval/publish_dataset.py",
    "eval/scorers.py",
    "docs/logs/20260813-archive-original-eval-results/",
)
LINEAGE_FILES = (
    "eval/run_eval.py",
    "eval/agent_model.py",
)
LINEAGE_TEST_FILE = "eval/tests/test_evaluation_lineage.py"
LINEAGE_HELPER_NAMES = {
    "load_evaluation_lineage",
    "evaluation_display_name",
    "trace_lineage_attributes",
}
LINEAGE_CONSTANT_NAMES = {
    "REQUIRED_LINEAGE_KEYS",
    "TRACE_LINEAGE_KEYS",
}
EXPECTED_REQUIRED_LINEAGE_KEYS = (
    "eval.improvement.source_evaluation_id",
    "eval.improvement.trial_id",
    "eval.improvement.target_metric",
    "eval.improvement.direction",
    "eval.improvement.hypothesis",
    "eval.improvement.change_summary",
    "eval.improvement.changed_files",
    "git.worktree_id",
    "git.worktree.path",
    "git.branch",
    "git.base_commit",
    "git.candidate_commit",
)
EXPECTED_TRACE_LINEAGE_KEYS = {
    "eval.improvement.source_evaluation_id": (
        "weave.eval.improvement.source_evaluation_id"
    ),
    "eval.improvement.trial_id": "weave.eval.improvement.trial_id",
    "eval.improvement.target_metric": "weave.eval.improvement.target_metric",
    "eval.improvement.direction": "weave.eval.improvement.direction",
    "eval.improvement.change_summary": "weave.eval.improvement.change_summary",
    "git.candidate_commit": "weave.eval.improvement.candidate_commit",
}
PROTECTED_RUNTIME_NAMES = {
    "client",
    "dataset",
    "dataset_uri",
    "errors",
    "example",
    "example_id",
    "model",
    "output",
    "result",
    "scorer_attributes",
    "scorer_names",
    "scorers",
    "settings",
}
PROTECTED_CALLS = {
    "apply_and_log_scorer",
    "apply_scorer_async",
    "build_scorers",
    "eval_logger.fail",
    "eval_logger.log_prediction",
    "eval_logger.log_summary",
    "get_scorer_attributes",
    "load_settings",
    "model.predict",
    "prediction.log_score",
    "weave.init",
    "weave.ref",
}


class TrialError(RuntimeError):
    """Raised when a trial operation would be unsafe or ambiguous."""


def _run_git(
    repo: Path,
    args: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _repo_root(repo: Path) -> Path:
    result = _run_git(repo, ["rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip()).resolve()


def _status(repo: Path) -> str:
    return _run_git(
        repo,
        ["status", "--porcelain=v1", "--untracked-files=all"],
    ).stdout


def _status_digest(status: str) -> str:
    return hashlib.sha256(status.encode()).hexdigest()


def _resolve_commit(repo: Path, ref: str) -> str:
    result = _run_git(repo, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    commit = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise TrialError(f"Git ref did not resolve to a full commit SHA: {ref}")
    return commit


def _source_short(source_evaluation_id: str) -> str:
    normalized = "".join(
        char.lower() for char in source_evaluation_id if char.isalnum()
    )
    if len(normalized) < 6:
        raise TrialError("Source Evaluation ID must contain at least 6 alphanumerics")
    return normalized[:12]


def _required_text(value: str, name: str, *, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise TrialError(f"{name} must not be empty")
    if len(normalized) > max_length:
        raise TrialError(f"{name} must be at most {max_length} characters")
    return normalized


def _validate_call_url(url: str, evaluation_id: str) -> str:
    normalized = _required_text(url, "Evaluation URL", max_length=2000)
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        raise TrialError("Evaluation URL must be an absolute URL")
    if not parsed.path.rstrip("/").endswith(f"/r/call/{evaluation_id}"):
        raise TrialError("Evaluation URL does not match the Evaluation ID")
    return normalized


def _state_path(repo: Path, trial_id: str) -> Path:
    return repo / "tmp" / "eval-improvements" / f"{trial_id}.json"


def _state_lock_path(repo: Path, trial_id: str) -> Path:
    return repo / "tmp" / "eval-improvements" / f"{trial_id}.lock"


@contextmanager
def _state_lock(repo: Path, trial_id: str) -> Iterator[None]:
    if not TRIAL_PATTERN.fullmatch(trial_id):
        raise TrialError(f"Invalid trial ID: {trial_id}")
    path = _state_lock_path(repo, trial_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _git_file(repo: Path, commit: str, path: str) -> str | None:
    result = _run_git(repo, ["show", f"{commit}:{path}"], check=False)
    return result.stdout if result.returncode == 0 else None


def _has_lineage_plumbing(repo: Path, commit: str) -> bool:
    run_eval = _git_file(repo, commit, "eval/run_eval.py") or ""
    agent_model = _git_file(repo, commit, "eval/agent_model.py") or ""
    return all(
        token in run_eval
        for token in (
            "WEAVE_EVAL_LINEAGE_JSON",
            "eval_attributes",
            "prediction.evaluate_call.id",
            "[evaluation_id]",
            "improve:",
        )
    ) and all(
        token in agent_model
        for token in (
            "improvement_trial_id",
            "source_evaluation_id",
            "candidate_commit",
            "change_summary",
        )
    )


def _lineage_plumbing_required(repo: Path, commit: str) -> bool:
    has_relevant_runner = any(
        _git_file(repo, commit, path) is not None for path in LINEAGE_FILES
    )
    return has_relevant_runner and not _has_lineage_plumbing(repo, commit)


def _parse_python(source: str, path: str) -> ast.Module:
    try:
        return ast.parse(source, filename=path)
    except SyntaxError as error:
        raise TrialError(f"Could not parse {path}: {error}") from error


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _target_root_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        roots: set[str] = set()
        for element in node.elts:
            roots.update(_target_root_names(element))
        return roots
    if isinstance(node, ast.Starred):
        return _target_root_names(node.value)
    if isinstance(node, ast.Attribute):
        name = _qualified_name(node)
        return {name.split(".", 1)[0]} if name else set()
    if isinstance(node, ast.Subscript):
        return _target_root_names(node.value)
    return set()


def _assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        return [node.target]
    if isinstance(node, ast.Delete):
        return list(node.targets)
    return []


def _protected_assignment_fingerprints(tree: ast.Module) -> list[str]:
    fingerprints: list[str] = []
    for node in ast.walk(tree):
        targets = _assignment_targets(node)
        if (
            isinstance(node, ast.Assign)
            and any(_target_root_names(target) == {"model"} for target in targets)
            and isinstance(node.value, ast.Call)
            and _qualified_name(node.value.func) == "SlideAgentModel"
        ):
            continue
        if targets and any(
            _target_root_names(target) & PROTECTED_RUNTIME_NAMES
            for target in targets
        ):
            fingerprints.append(ast.dump(node, include_attributes=False))
    return sorted(fingerprints)


def _protected_call_fingerprints(tree: ast.Module) -> list[str]:
    fingerprints: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _qualified_name(node.func)
        root = name.split(".", 1)[0] if name else None
        if name in PROTECTED_CALLS or root in PROTECTED_RUNTIME_NAMES:
            fingerprints.append(ast.dump(node, include_attributes=False))
    return sorted(fingerprints)


def _loop_fingerprints(tree: ast.Module) -> list[str]:
    fingerprints: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            fingerprints.append(
                ast.dump(
                    ast.Tuple(
                        elts=[copy.deepcopy(node.target), copy.deepcopy(node.iter)],
                        ctx=ast.Load(),
                    ),
                    include_attributes=False,
                )
            )
    return sorted(fingerprints)


def _find_calls(tree: ast.Module, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _qualified_name(node.func) == name
    ]


def _literal_module_assignment(tree: ast.Module, name: str) -> Any:
    values: list[ast.AST] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = _assignment_targets(node)
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            value = node.value
            if value is not None:
                values.append(value)
    if len(values) != 1:
        raise TrialError(f"Expected exactly one {name} assignment")
    try:
        return ast.literal_eval(values[0])
    except (ValueError, TypeError) as error:
        raise TrialError(f"{name} must be a literal value") from error


def _validate_context_base_fields(
    base_tree: ast.Module, candidate_tree: ast.Module
) -> None:
    def returned_dict(tree: ast.Module) -> ast.Dict | None:
        function = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "build_eval_context"
            ),
            None,
        )
        if function is None:
            return None
        dictionaries = [
            node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
        ]
        if len(dictionaries) != 1:
            raise TrialError("build_eval_context must return one dictionary literal")
        return dictionaries[0]

    base_dict = returned_dict(base_tree)
    candidate_dict = returned_dict(candidate_tree)
    if base_dict is None and candidate_dict is None:
        return
    if base_dict is None or candidate_dict is None:
        raise TrialError("build_eval_context structure changed")

    def entries(value: ast.Dict) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, item in zip(value.keys, value.values, strict=True):
            if key is None:
                continue
            try:
                literal_key = ast.literal_eval(key)
            except (ValueError, TypeError):
                continue
            if isinstance(literal_key, str):
                result[literal_key] = ast.dump(item, include_attributes=False)
        return result

    base_entries = entries(base_dict)
    candidate_entries = entries(candidate_dict)
    mismatched = [
        key
        for key, value in base_entries.items()
        if candidate_entries.get(key) != value
    ]
    if mismatched:
        raise TrialError(
            "build_eval_context changed existing fields: " + ", ".join(mismatched)
        )


def _validate_trace_lineage_wiring(tree: ast.Module) -> None:
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "build_eval_context"
        ),
        None,
    )
    if function is None:
        raise TrialError("build_eval_context is required for trace lineage")
    if [arg.arg for arg in function.args.kwonlyargs].count("lineage") != 1:
        raise TrialError("build_eval_context must accept one keyword-only lineage")
    dictionaries = [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    ]
    if len(dictionaries) != 1:
        raise TrialError("build_eval_context must return one dictionary literal")
    lineage_unpacks = [
        value
        for key, value in zip(
            dictionaries[0].keys, dictionaries[0].values, strict=True
        )
        if key is None
        and isinstance(value, ast.Call)
        and _qualified_name(value.func) == "trace_lineage_attributes"
        and [arg.id if isinstance(arg, ast.Name) else None for arg in value.args]
        == ["lineage"]
        and not value.keywords
    ]
    if len(lineage_unpacks) != 1:
        raise TrialError(
            "build_eval_context must unpack trace_lineage_attributes(lineage)"
        )

    calls = _find_calls(tree, "build_eval_context")
    if not calls:
        raise TrialError("build_eval_context has no call site")
    for call in calls:
        keywords = _keyword_map(call)
        if _qualified_name(keywords.get("lineage", ast.Constant(None))) != "lineage":
            raise TrialError("build_eval_context calls must pass lineage=lineage")
        if (
            _qualified_name(keywords.get("evaluation_name", ast.Constant(None)))
            != "evaluation_name"
        ):
            raise TrialError(
                "build_eval_context calls must pass evaluation_name"
            )


def _is_lineage_assignment(node: ast.Assign) -> bool:
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return False
    target = node.targets[0].id
    if target == "current_evaluation_id":
        return ast.dump(node.value, include_attributes=False) == ast.dump(
            ast.Attribute(
                value=ast.Attribute(
                    value=ast.Name(id="prediction", ctx=ast.Load()),
                    attr="evaluate_call",
                    ctx=ast.Load(),
                ),
                attr="id",
                ctx=ast.Load(),
            ),
            include_attributes=False,
        )
    if not isinstance(node.value, ast.Call) or node.value.keywords:
        return False
    if target == "lineage":
        return (
            _qualified_name(node.value.func) == "load_evaluation_lineage"
            and not node.value.args
        )
    if target == "evaluation_name":
        return (
            _qualified_name(node.value.func) == "evaluation_display_name"
            and [
                arg.id if isinstance(arg, ast.Name) else None
                for arg in node.value.args
            ]
            == ["variant", "lineage"]
        )
    return False


def _is_evaluation_id_guard(node: ast.If) -> bool:
    if node.orelse or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if (
        not isinstance(test.left, ast.Name)
        or test.left.id != "evaluation_id"
        or len(test.ops) != 1
        or len(test.comparators) != 1
        or len(node.body) != 1
    ):
        return False
    comparator = test.comparators[0]
    statement = node.body[0]
    is_setter = (
        isinstance(test.ops[0], ast.Is)
        and isinstance(comparator, ast.Constant)
        and comparator.value is None
        and isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == "evaluation_id"
        and isinstance(statement.value, ast.Name)
        and statement.value.id == "current_evaluation_id"
    )
    is_verifier = (
        isinstance(test.ops[0], ast.NotEq)
        and isinstance(comparator, ast.Name)
        and comparator.id == "current_evaluation_id"
        and isinstance(statement, ast.Raise)
        and isinstance(statement.exc, ast.Call)
        and _qualified_name(statement.exc.func) == "RuntimeError"
        and statement.cause is None
    )
    return is_setter or is_verifier


def _is_evaluation_id_print(node: ast.If) -> bool:
    if not isinstance(node.test, ast.Name) or node.test.id != "evaluation_id":
        return False
    if len(node.body) != 1 or node.orelse:
        return False
    statement = node.body[0]
    if not isinstance(statement, ast.Expr) or not isinstance(
        statement.value, ast.Call
    ):
        return False
    call = statement.value
    if _qualified_name(call.func) != "print" or len(call.args) != 1:
        return False
    value = call.args[0]
    return (
        isinstance(value, ast.JoinedStr)
        and len(value.values) == 2
        and isinstance(value.values[0], ast.Constant)
        and value.values[0].value == "[evaluation_id] "
        and isinstance(value.values[1], ast.FormattedValue)
        and isinstance(value.values[1].value, ast.Name)
        and value.values[1].value.id == "evaluation_id"
        and not call.keywords
    )


class _RunEvalLineageNormalizer(ast.NodeTransformer):
    """Remove only the canonical score-neutral lineage additions."""

    def visit_Module(self, node: ast.Module) -> ast.Module:
        retained: list[ast.stmt] = []
        for item in node.body:
            if isinstance(item, ast.Import) and all(
                alias.name in {"json", "os", "subprocess"}
                for alias in item.names
            ):
                continue
            if isinstance(item, (ast.Assign, ast.AnnAssign)) and any(
                isinstance(target, ast.Name)
                and target.id in LINEAGE_CONSTANT_NAMES
                for target in _assignment_targets(item)
            ):
                continue
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                item.name in LINEAGE_HELPER_NAMES
            ):
                continue
            retained.append(item)
        node.body = retained
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._visit_function(node)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> ast.AST:
        if node.name == "build_eval_context":
            pairs = list(zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True))
            retained = [pair for pair in pairs if pair[0].arg != "lineage"]
            node.args.kwonlyargs = [pair[0] for pair in retained]
            node.args.kw_defaults = [pair[1] for pair in retained]
        if node.name == "run_evaluation" and isinstance(
            node.returns, ast.Subscript
        ):
            slice_value = node.returns.slice
            if isinstance(slice_value, ast.Tuple) and len(slice_value.elts) == 3:
                slice_value.elts = slice_value.elts[:2]
        return self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> ast.AST | None:
        if _is_lineage_assignment(node):
            return None
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Tuple)
            and [
                target.id if isinstance(target, ast.Name) else None
                for target in node.targets[0].elts
            ]
            == ["evaluation_url", "errors", "evaluation_id"]
        ):
            node.targets[0].elts = node.targets[0].elts[:2]
        return self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST | None:
        if (
            isinstance(node.target, ast.Name)
            and node.target.id == "evaluation_id"
            and isinstance(node.value, ast.Constant)
            and node.value.value is None
        ):
            return None
        return self.generic_visit(node)

    def visit_If(self, node: ast.If) -> ast.AST | None:
        if _is_evaluation_id_guard(node) or _is_evaluation_id_print(node):
            return None
        return self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> ast.AST:
        pairs = [
            (key, value)
            for key, value in zip(node.keys, node.values, strict=True)
            if not (
                key is None
                and isinstance(value, ast.Call)
                and _qualified_name(value.func) == "trace_lineage_attributes"
            )
        ]
        node.keys = [key for key, _value in pairs]
        node.values = [value for _key, value in pairs]
        return self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> ast.AST:
        name = _qualified_name(node.func)
        if name == "SlideAgentModel":
            node.keywords = [
                keyword
                for keyword in node.keywords
                if keyword.arg
                not in {
                    "improvement_trial_id",
                    "source_evaluation_id",
                    "candidate_commit",
                    "change_summary",
                }
            ]
        elif name == "weave.EvaluationLogger":
            node.keywords = [
                keyword
                for keyword in node.keywords
                if keyword.arg != "eval_attributes"
            ]
            for keyword in node.keywords:
                if keyword.arg == "name":
                    keyword.value = ast.Name(id="variant", ctx=ast.Load())
        elif name == "build_eval_context":
            node.keywords = [
                keyword for keyword in node.keywords if keyword.arg != "lineage"
            ]
            for keyword in node.keywords:
                if keyword.arg == "evaluation_name":
                    keyword.value = ast.Name(id="variant", ctx=ast.Load())
        return self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> ast.AST:
        if isinstance(node.value, ast.Tuple) and len(node.value.elts) == 3:
            if (
                isinstance(node.value.elts[-1], ast.Name)
                and node.value.elts[-1].id == "evaluation_id"
            ):
                node.value.elts = node.value.elts[:2]
        return self.generic_visit(node)


def _normalized_run_eval(tree: ast.Module) -> str:
    normalized = _RunEvalLineageNormalizer().visit(copy.deepcopy(tree))
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, include_attributes=False)


def _validate_evaluation_id_capture(tree: ast.Module) -> None:
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "run_evaluation"
        ),
        None,
    )
    if function is None:
        raise TrialError("run_evaluation is missing")
    initializers = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "evaluation_id"
        and isinstance(node.value, ast.Constant)
        and node.value.value is None
    ]
    current_ids = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "current_evaluation_id"
        and _is_lineage_assignment(node)
    ]
    guards = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.If) and _is_evaluation_id_guard(node)
    ]
    returns = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Tuple)
        and len(node.value.elts) == 3
        and isinstance(node.value.elts[-1], ast.Name)
        and node.value.elts[-1].id == "evaluation_id"
    ]
    prints = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and _is_evaluation_id_print(node)
    ]
    if not (
        len(initializers) == 1
        and len(current_ids) == 1
        and len(guards) == 2
        and len(returns) == 1
        and len(prints) == 1
    ):
        raise TrialError(
            "Evaluation ID capture must initialize, verify every prediction, "
            "return, and print one stable root ID"
        )


def _keyword_map(call: ast.Call) -> dict[str, ast.AST]:
    if any(keyword.arg is None for keyword in call.keywords):
        raise TrialError("Lineage calls cannot use **kwargs expansion")
    return {str(keyword.arg): keyword.value for keyword in call.keywords}


def _validate_special_constructor(
    base_tree: ast.Module, candidate_tree: ast.Module, call_name: str
) -> None:
    base_calls = _find_calls(base_tree, call_name)
    candidate_calls = _find_calls(candidate_tree, call_name)
    if len(base_calls) != 1 or len(candidate_calls) != 1:
        raise TrialError(f"Expected exactly one {call_name} call")
    base_call = base_calls[0]
    candidate_call = candidate_calls[0]
    if [ast.dump(arg) for arg in base_call.args] != [
        ast.dump(arg) for arg in candidate_call.args
    ]:
        raise TrialError(f"{call_name} positional arguments changed")

    base_keywords = _keyword_map(base_call)
    candidate_keywords = _keyword_map(candidate_call)
    if call_name == "SlideAgentModel":
        allowed_additions = {
            "improvement_trial_id",
            "source_evaluation_id",
            "candidate_commit",
            "change_summary",
        }
        immutable_keys = set(base_keywords)
        if set(candidate_keywords) != set(base_keywords) | allowed_additions:
            raise TrialError("SlideAgentModel must receive all four lineage fields")
        expected_model_keys = {
            "improvement_trial_id": "eval.improvement.trial_id",
            "source_evaluation_id": "eval.improvement.source_evaluation_id",
            "candidate_commit": "git.candidate_commit",
            "change_summary": "eval.improvement.change_summary",
        }
        for key, expected_key in expected_model_keys.items():
            value = candidate_keywords[key]
            if not (
                isinstance(value, ast.Call)
                and _qualified_name(value.func) == "lineage.get"
                and len(value.args) == 1
                and isinstance(value.args[0], ast.Constant)
                and value.args[0].value == expected_key
                and not value.keywords
            ):
                raise TrialError(
                    f"SlideAgentModel {key} has the wrong lineage source"
                )
    else:
        allowed_additions = {"eval_attributes"}
        immutable_keys = set(base_keywords) - {"name"}
        if set(candidate_keywords) != set(base_keywords) | allowed_additions:
            raise TrialError("EvaluationLogger arguments changed beyond lineage")
        if _qualified_name(candidate_keywords["eval_attributes"]) != "lineage":
            raise TrialError("EvaluationLogger eval_attributes must use lineage")
        if _qualified_name(candidate_keywords["name"]) != "evaluation_name":
            raise TrialError("EvaluationLogger name must use evaluation_name")

    if not set(base_keywords).issubset(candidate_keywords):
        raise TrialError(f"{call_name} removed existing arguments")
    if set(candidate_keywords) - set(base_keywords) - allowed_additions:
        raise TrialError(f"{call_name} added non-lineage arguments")
    for key in immutable_keys:
        if ast.dump(base_keywords[key]) != ast.dump(candidate_keywords[key]):
            raise TrialError(f"{call_name} changed protected argument: {key}")


def _normalized_agent_model(source: str) -> str:
    tree = _parse_python(source, "eval/agent_model.py")
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "SlideAgentModel":
            continue
        node.body = [
            item
            for item in node.body
            if not (
                isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
                and item.target.id
                in {
                    "improvement_trial_id",
                    "source_evaluation_id",
                    "candidate_commit",
                    "change_summary",
                }
            )
        ]
    return ast.dump(tree, include_attributes=False)


def _validate_agent_model_lineage_fields(source: str) -> None:
    tree = _parse_python(source, "eval/agent_model.py")
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SlideAgentModel"
    ]
    if len(classes) != 1:
        raise TrialError("Expected exactly one SlideAgentModel class")
    expected_names = {
        "improvement_trial_id",
        "source_evaluation_id",
        "candidate_commit",
        "change_summary",
    }
    fields = [
        node
        for node in classes[0].body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in expected_names
    ]
    if len(fields) != len(expected_names) or {
        field.target.id for field in fields if isinstance(field.target, ast.Name)
    } != expected_names:
        raise TrialError("SlideAgentModel must define each lineage field once")
    expected_annotation = ast.dump(
        ast.parse("str | None", mode="eval").body,
        include_attributes=False,
    )
    invalid = [
        field.target.id
        for field in fields
        if ast.dump(field.annotation, include_attributes=False)
        != expected_annotation
        or not isinstance(field.value, ast.Constant)
        or field.value.value is not None
        or field.simple != 1
    ]
    if invalid:
        raise TrialError(
            "SlideAgentModel lineage fields must use str | None = None: "
            + ", ".join(sorted(invalid))
        )


def _validate_initial_lineage_patch(
    repo: Path,
    base_commit: str,
    candidate_commit: str,
    changed_files: list[str],
) -> None:
    allowed_eval_paths = {*LINEAGE_FILES, LINEAGE_TEST_FILE}
    unexpected = sorted(
        path
        for path in changed_files
        if path.startswith("eval/") and path not in allowed_eval_paths
    )
    if unexpected:
        raise TrialError(
            "Initial lineage patch changed protected eval paths: "
            + ", ".join(unexpected)
        )

    base_test = _git_file(repo, base_commit, LINEAGE_TEST_FILE)
    if base_test is None and LINEAGE_TEST_FILE not in changed_files:
        raise TrialError(
            f"Initial lineage patch must add {LINEAGE_TEST_FILE}"
        )
    if base_test is not None and LINEAGE_TEST_FILE in changed_files:
        raise TrialError("Existing lineage contract tests are protected")

    base_model = _git_file(repo, base_commit, "eval/agent_model.py")
    candidate_model = _git_file(repo, candidate_commit, "eval/agent_model.py")
    base_runner = _git_file(repo, base_commit, "eval/run_eval.py")
    candidate_runner = _git_file(repo, candidate_commit, "eval/run_eval.py")
    if None in (base_model, candidate_model, base_runner, candidate_runner):
        raise TrialError("Initial lineage patch requires both Evaluation code files")
    assert base_model is not None and candidate_model is not None
    assert base_runner is not None and candidate_runner is not None

    _validate_agent_model_lineage_fields(candidate_model)
    if _normalized_agent_model(base_model) != _normalized_agent_model(
        candidate_model
    ):
        raise TrialError(
            "eval/agent_model.py changed beyond the four immutable lineage fields"
        )

    base_tree = _parse_python(base_runner, "eval/run_eval.py")
    candidate_tree = _parse_python(candidate_runner, "eval/run_eval.py")
    for extractor, label in (
        (_protected_assignment_fingerprints, "protected assignments"),
        (_protected_call_fingerprints, "protected calls"),
        (_loop_fingerprints, "evaluation loops"),
    ):
        if extractor(base_tree) != extractor(candidate_tree):
            raise TrialError(f"eval/run_eval.py changed {label}")

    _validate_special_constructor(
        base_tree, candidate_tree, "SlideAgentModel"
    )
    _validate_special_constructor(
        base_tree, candidate_tree, "weave.EvaluationLogger"
    )
    _validate_context_base_fields(base_tree, candidate_tree)
    _validate_trace_lineage_wiring(candidate_tree)
    _validate_evaluation_id_capture(candidate_tree)
    if _normalized_run_eval(candidate_tree) != ast.dump(
        base_tree, include_attributes=False
    ):
        raise TrialError(
            "eval/run_eval.py changed control flow or statements beyond lineage"
        )

    base_defs = {
        node.name
        for node in base_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    candidate_defs = {
        node.name
        for node in candidate_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    if not base_defs.issubset(candidate_defs):
        raise TrialError("eval/run_eval.py removed an existing definition")
    if candidate_defs - base_defs != LINEAGE_HELPER_NAMES:
        raise TrialError(
            "eval/run_eval.py must add exactly the three lineage helpers"
        )

    base_assignments = {
        target.id
        for node in base_tree.body
        for target in _assignment_targets(node)
        if isinstance(target, ast.Name)
    }
    candidate_assignments = {
        target.id
        for node in candidate_tree.body
        for target in _assignment_targets(node)
        if isinstance(target, ast.Name)
    }
    if candidate_assignments - base_assignments != LINEAGE_CONSTANT_NAMES:
        raise TrialError(
            "eval/run_eval.py must add exactly the two lineage constants"
        )
    required_keys = _literal_module_assignment(
        candidate_tree, "REQUIRED_LINEAGE_KEYS"
    )
    if not isinstance(required_keys, (list, tuple)) or tuple(
        required_keys
    ) != EXPECTED_REQUIRED_LINEAGE_KEYS:
        raise TrialError("REQUIRED_LINEAGE_KEYS does not match the lineage contract")
    trace_keys = _literal_module_assignment(candidate_tree, "TRACE_LINEAGE_KEYS")
    if trace_keys != EXPECTED_TRACE_LINEAGE_KEYS:
        raise TrialError("TRACE_LINEAGE_KEYS does not match the trace contract")

    base_imports = {
        alias.name
        for node in base_tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    candidate_imports = {
        alias.name
        for node in candidate_tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    if not base_imports.issubset(candidate_imports):
        raise TrialError("eval/run_eval.py removed an existing import")
    if candidate_imports - base_imports != {"json", "os", "subprocess"}:
        raise TrialError(
            "eval/run_eval.py must add exactly json, os, and subprocess imports"
        )


def _matches_prefix(path: str, prefix: str) -> bool:
    return path == prefix or (prefix.endswith("/") and path.startswith(prefix))


def _protected_changes(state: dict[str, Any], changed_files: list[str]) -> list[str]:
    prefixes = state["evaluation_contract"]["protected_prefixes"]
    return sorted(
        path
        for path in changed_files
        if any(_matches_prefix(path, prefix) for prefix in prefixes)
    )


def _expected_lineage(
    state: dict[str, Any], *, candidate_commit: str, changed_files: list[str]
) -> dict[str, Any]:
    return {
        "eval.improvement.source_evaluation_id": state["source_evaluation_id"],
        "eval.improvement.trial_id": state["trial_id"],
        "eval.improvement.target_metric": state["improvement"]["target_metric"],
        "eval.improvement.direction": state["improvement"]["direction"],
        "eval.improvement.hypothesis": state["improvement"]["hypothesis"],
        "eval.improvement.change_summary": state["improvement"]["change_summary"],
        "eval.improvement.changed_files": changed_files,
        "git.worktree_id": state["worktree"]["id"],
        "git.worktree.path": state["worktree"]["relative_path"],
        "git.branch": state["worktree"]["branch"],
        "git.base_commit": state["worktree"]["base_commit"],
        "git.candidate_commit": candidate_commit,
    }


def _fetch_evaluation_attributes(evaluation_id: str, evaluation_url: str) -> dict[str, Any]:
    try:
        import weave
        from inspect_evaluation import _plain, _short_op, parse_locator

        project, parsed_id = parse_locator(evaluation_url, None)
        if parsed_id != evaluation_id:
            raise TrialError("Evaluation URL does not resolve to the supplied ID")
        call = weave.init(project).get_call(evaluation_id)
        if _short_op(getattr(call, "op_name", None)) != "Evaluation.evaluate":
            raise TrialError("Candidate call is not an Evaluation.evaluate root")
        attributes = _plain(getattr(call, "attributes", None))
        if not isinstance(attributes, dict):
            raise TrialError("Candidate Evaluation has no readable attributes")
        return attributes
    except TrialError:
        raise
    except Exception as error:
        raise TrialError(f"Could not verify candidate Evaluation lineage: {error}") from error


def _verify_remote_lineage(
    state: dict[str, Any], evaluation_id: str, evaluation_url: str
) -> None:
    expected = _expected_lineage(
        state,
        candidate_commit=state["candidate"]["commit"],
        changed_files=state["improvement"]["changed_files"],
    )
    observed = _fetch_evaluation_attributes(evaluation_id, evaluation_url)
    mismatches = {
        key: {"expected": value, "observed": observed.get(key)}
        for key, value in expected.items()
        if observed.get(key) != value
    }
    if mismatches:
        raise TrialError(
            "Candidate Evaluation lineage does not match the reserved trial: "
            + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
        )


def _load_state(repo: Path, trial_id: str) -> tuple[Path, dict[str, Any]]:
    if not TRIAL_PATTERN.fullmatch(trial_id):
        raise TrialError(f"Invalid trial ID: {trial_id}")
    path = _state_path(repo, trial_id)
    if not path.is_file():
        raise TrialError(f"Trial state does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("trial_id") != trial_id:
        raise TrialError(f"Invalid trial state: {path}")
    return path, value


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _branch_exists(repo: Path, branch: str) -> bool:
    result = _run_git(
        repo,
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    )
    return result.returncode == 0


def _is_ignored(repo: Path, relative_path: Path) -> bool:
    result = _run_git(
        repo,
        ["check-ignore", "--quiet", "--no-index", "--", relative_path.as_posix()],
        check=False,
    )
    return result.returncode == 0


def create_trial(args: argparse.Namespace) -> dict[str, Any]:
    repo = _repo_root(Path(args.repo).resolve())
    source_id = args.source_evaluation_id.strip()
    if not EVALUATION_ID_PATTERN.fullmatch(source_id):
        raise TrialError("Source Evaluation ID has an invalid shape")
    source_url = _validate_call_url(args.source_evaluation_url, source_id)

    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    if not re.fullmatch(r"\d{14}", timestamp):
        raise TrialError("Timestamp must use UTC YYYYMMDDHHMMSS format")
    trial_id = f"eval-{_source_short(source_id)}-{timestamp}"
    if not TRIAL_PATTERN.fullmatch(trial_id):
        raise TrialError(f"Generated invalid trial ID: {trial_id}")

    base_commit = _resolve_commit(repo, args.base_ref)
    lineage_plumbing_required = _lineage_plumbing_required(repo, base_commit)
    protected_prefixes = list(ALWAYS_PROTECTED_PREFIXES)
    if not lineage_plumbing_required:
        protected_prefixes.append("eval/")
    target_metric = _required_text(
        args.target_metric, "Target metric", max_length=300
    )
    hypothesis = _required_text(args.hypothesis, "Hypothesis", max_length=2000)
    change_summary = _required_text(
        args.change_summary, "Change summary", max_length=500
    )
    branch = f"eval-improve/{trial_id}"
    relative_worktree = Path("tmp") / "eval-worktrees" / trial_id
    worktree = repo / relative_worktree
    state_path = _state_path(repo, trial_id)
    relative_state = state_path.relative_to(repo)

    if not _is_ignored(repo, relative_worktree) or not _is_ignored(
        repo, relative_state
    ):
        raise TrialError(
            "tmp/eval-worktrees and tmp/eval-improvements must be ignored by Git"
        )

    state: dict[str, Any]
    with _state_lock(repo, trial_id):
        if worktree.exists():
            raise TrialError(f"Worktree path already exists: {worktree}")
        if state_path.exists():
            raise TrialError(f"Trial state already exists: {state_path}")
        if _branch_exists(repo, branch):
            raise TrialError(f"Trial branch already exists: {branch}")

        status_before = _status(repo)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        _run_git(
            repo,
            ["worktree", "add", "-b", branch, str(worktree), base_commit],
        )
        status_after = _status(repo)
        if status_after != status_before:
            raise TrialError(
                "Original worktree status changed while creating the linked "
                f"worktree; the retained worktree is at {worktree}"
            )

        state = {
            "schema_version": 1,
            "status": "created",
            "trial_id": trial_id,
            "source_evaluation_id": source_id,
            "source_evaluation_url": source_url,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(repo),
            "primary_status_sha256": _status_digest(status_before),
            "worktree": {
                "id": trial_id,
                "path": str(worktree),
                "relative_path": relative_worktree.as_posix(),
                "branch": branch,
                "base_ref": args.base_ref,
                "base_commit": base_commit,
            },
            "candidate": {
                "attempted": False,
                "started_at": None,
                "commit": None,
                "evaluation_id": None,
                "evaluation_url": None,
                "error": None,
                "head_at_record": None,
                "head_unchanged_at_record": None,
                "worktree_clean_at_record": None,
            },
            "evaluation_contract": {
                "lineage_plumbing_required": lineage_plumbing_required,
                "protected_prefixes": protected_prefixes,
            },
            "improvement": {
                "target_metric": target_metric,
                "direction": args.direction,
                "hypothesis": hypothesis,
                "change_summary": change_summary,
                "changed_files": [],
            },
            "comparison": None,
        }
        _write_state(state_path, state)
    return {**state, "state_path": str(state_path)}


def trial_status(args: argparse.Namespace) -> dict[str, Any]:
    repo = _repo_root(Path(args.repo).resolve())
    state_path, state = _load_state(repo, args.trial_id)
    worktree = Path(state["worktree"]["path"])
    if not worktree.is_dir():
        raise TrialError(f"Retained worktree is missing: {worktree}")

    branch = _run_git(worktree, ["branch", "--show-current"]).stdout.strip()
    head = _resolve_commit(worktree, "HEAD")
    worktree_status = _status(worktree)
    changed_files = [
        line
        for line in _run_git(
            worktree,
            [
                "diff",
                "--no-renames",
                "--name-only",
                f"{state['worktree']['base_commit']}..{head}",
            ],
        ).stdout.splitlines()
        if line
    ]
    primary_status = _status(repo)
    expected_branch = state["worktree"]["branch"]
    if branch != expected_branch:
        raise TrialError(
            f"Worktree branch mismatch: expected {expected_branch}, got {branch}"
        )

    lineage = _expected_lineage(
        state, candidate_commit=head, changed_files=changed_files
    )
    return {
        **state,
        "state_path": str(state_path),
        "observed": {
            "branch": branch,
            "head": head,
            "changed_files": changed_files,
            "worktree_clean": worktree_status == "",
            "primary_status_unchanged": _status_digest(primary_status)
            == state["primary_status_sha256"],
            "lineage": lineage,
        },
    }


def start_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    repo = _repo_root(Path(args.repo).resolve())
    with _state_lock(repo, args.trial_id):
        state_path, state = _load_state(repo, args.trial_id)
        worktree = Path(state["worktree"]["path"])
        if not worktree.is_dir():
            raise TrialError(f"Retained worktree is missing: {worktree}")
        if state["candidate"].get("attempted"):
            raise TrialError("This one-trial record already has a candidate attempt")
        if _status(worktree):
            raise TrialError("Candidate worktree has uncommitted or untracked changes")
        if _status_digest(_status(repo)) != state["primary_status_sha256"]:
            raise TrialError("Original worktree status changed before Evaluation start")

        candidate_commit = _resolve_commit(worktree, "HEAD")
        base_commit = state["worktree"]["base_commit"]
        changed_files = [
            line
            for line in _run_git(
                worktree,
                [
                    "diff",
                    "--no-renames",
                    "--name-only",
                    f"{base_commit}..{candidate_commit}",
                ],
            ).stdout.splitlines()
            if line
        ]
        if candidate_commit == base_commit or not changed_files:
            raise TrialError("Candidate must contain at least one committed change")

        protected = _protected_changes(state, changed_files)
        if protected:
            raise TrialError(
                "Candidate changes protected evaluation-contract files: "
                + ", ".join(protected)
            )
        if state["evaluation_contract"]["lineage_plumbing_required"]:
            if not _has_lineage_plumbing(worktree, candidate_commit):
                raise TrialError(
                    "Candidate must add the required score-neutral Evaluation "
                    "lineage plumbing before Evaluation start"
                )
            _validate_initial_lineage_patch(
                worktree, base_commit, candidate_commit, changed_files
            )

        state["status"] = "evaluation-started"
        state["candidate"] = {
            "attempted": True,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "commit": candidate_commit,
            "evaluation_id": None,
            "evaluation_url": None,
            "error": None,
            "head_at_record": None,
            "head_unchanged_at_record": None,
            "worktree_clean_at_record": None,
        }
        state["improvement"]["changed_files"] = changed_files
        _write_state(state_path, state)
    return {**state, "state_path": str(state_path)}


def record_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    repo = _repo_root(Path(args.repo).resolve())
    with _state_lock(repo, args.trial_id):
        state_path, state = _load_state(repo, args.trial_id)
        worktree = Path(state["worktree"]["path"])
        if not worktree.is_dir():
            raise TrialError(f"Retained worktree is missing: {worktree}")
        if not state["candidate"].get("attempted"):
            raise TrialError("Reserve the one trial with start-evaluation first")
        if state["candidate"].get("evaluation_id") or state["candidate"].get(
            "error"
        ):
            raise TrialError("This candidate attempt already has a result")

        evaluation_id = args.evaluation_id.strip()
        if not EVALUATION_ID_PATTERN.fullmatch(evaluation_id):
            raise TrialError("Candidate Evaluation ID has an invalid shape")
        evaluation_url = _validate_call_url(args.evaluation_url, evaluation_id)
        _verify_remote_lineage(state, evaluation_id, evaluation_url)

        candidate_commit = state["candidate"]["commit"]
        head_at_record = _resolve_commit(worktree, "HEAD")
        worktree_clean = _status(worktree) == ""
        lineage_stable = head_at_record == candidate_commit and worktree_clean

        state["status"] = "evaluated" if lineage_stable else "inconclusive"
        state["candidate"] = {
            "attempted": True,
            "started_at": state["candidate"]["started_at"],
            "commit": candidate_commit,
            "evaluation_id": evaluation_id,
            "evaluation_url": evaluation_url,
            "error": None,
            "head_at_record": head_at_record,
            "head_unchanged_at_record": head_at_record == candidate_commit,
            "worktree_clean_at_record": worktree_clean,
        }
        if not lineage_stable:
            state["comparison"] = {
                "classification": "inconclusive",
                "reason": "candidate worktree changed after start-evaluation",
            }
        state["primary_status_unchanged_at_record"] = (
            _status_digest(_status(repo)) == state["primary_status_sha256"]
        )
        state["recorded_at"] = datetime.now(timezone.utc).isoformat()
        _write_state(state_path, state)
    return {**state, "state_path": str(state_path)}


def record_failure(args: argparse.Namespace) -> dict[str, Any]:
    repo = _repo_root(Path(args.repo).resolve())
    with _state_lock(repo, args.trial_id):
        state_path, state = _load_state(repo, args.trial_id)
        worktree = Path(state["worktree"]["path"])
        if not worktree.is_dir():
            raise TrialError(f"Retained worktree is missing: {worktree}")
        if not state["candidate"].get("attempted"):
            raise TrialError("Reserve the one trial with start-evaluation first")
        if state["candidate"].get("evaluation_id") or state["candidate"].get(
            "error"
        ):
            raise TrialError("This candidate attempt already has a result")

        candidate_commit = state["candidate"]["commit"]
        head_at_record = _resolve_commit(worktree, "HEAD")
        worktree_clean = _status(worktree) == ""
        error = _required_text(args.error, "Evaluation error", max_length=2000)
        evaluation_id = args.evaluation_id
        evaluation_url = args.evaluation_url
        if evaluation_id is not None:
            evaluation_id = evaluation_id.strip()
            if not EVALUATION_ID_PATTERN.fullmatch(evaluation_id):
                raise TrialError("Candidate Evaluation ID has an invalid shape")
            if evaluation_url is not None:
                evaluation_url = _validate_call_url(evaluation_url, evaluation_id)
        elif evaluation_url is not None:
            raise TrialError("An Evaluation URL requires its Evaluation ID")

        state["status"] = "failed"
        state["candidate"] = {
            "attempted": True,
            "started_at": state["candidate"]["started_at"],
            "commit": candidate_commit,
            "evaluation_id": evaluation_id,
            "evaluation_url": evaluation_url,
            "error": error,
            "head_at_record": head_at_record,
            "head_unchanged_at_record": head_at_record == candidate_commit,
            "worktree_clean_at_record": worktree_clean,
        }
        state["primary_status_unchanged_at_record"] = (
            _status_digest(_status(repo)) == state["primary_status_sha256"]
        )
        state["recorded_at"] = datetime.now(timezone.utc).isoformat()
        _write_state(state_path, state)
    return {**state, "state_path": str(state_path)}


def record_comparison(args: argparse.Namespace) -> dict[str, Any]:
    repo = _repo_root(Path(args.repo).resolve())
    with _state_lock(repo, args.trial_id):
        state_path, state = _load_state(repo, args.trial_id)
        if not state["candidate"].get("evaluation_id"):
            raise TrialError(
                "Record a successful candidate Evaluation before comparison"
            )
        if state.get("comparison") is not None:
            raise TrialError("This trial already has a comparison result")
        comparison = json.loads(args.comparison_json)
        if not isinstance(comparison, dict):
            raise TrialError("--comparison-json must decode to one JSON object")
        state["status"] = args.result_status
        state["comparison"] = comparison
        state["compared_at"] = datetime.now(timezone.utc).isoformat()
        _write_state(state_path, state)
    return {**state, "state_path": str(state_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create one retained worktree")
    create.add_argument("--source-evaluation-id", required=True)
    create.add_argument("--source-evaluation-url", required=True)
    create.add_argument("--base-ref", required=True)
    create.add_argument("--target-metric", required=True)
    create.add_argument(
        "--direction", required=True, choices=["maximize", "minimize"]
    )
    create.add_argument("--hypothesis", required=True)
    create.add_argument("--change-summary", required=True)
    create.add_argument("--repo", default=".")
    create.add_argument("--timestamp", help=argparse.SUPPRESS)
    create.set_defaults(handler=create_trial)

    status = subparsers.add_parser("status", help="inspect a retained trial")
    status.add_argument("--trial-id", required=True)
    status.add_argument("--repo", default=".")
    status.set_defaults(handler=trial_status)

    start = subparsers.add_parser(
        "start-evaluation", help="consume the one trial before launching it"
    )
    start.add_argument("--trial-id", required=True)
    start.add_argument("--repo", default=".")
    start.set_defaults(handler=start_evaluation)

    record = subparsers.add_parser(
        "record-evaluation", help="record the single candidate Evaluation"
    )
    record.add_argument("--trial-id", required=True)
    record.add_argument("--evaluation-id", required=True)
    record.add_argument("--evaluation-url", required=True)
    record.add_argument("--repo", default=".")
    record.set_defaults(handler=record_evaluation)

    failure = subparsers.add_parser(
        "record-failure", help="record a failed single Evaluation attempt"
    )
    failure.add_argument("--trial-id", required=True)
    failure.add_argument("--error", required=True)
    failure.add_argument("--evaluation-id")
    failure.add_argument("--evaluation-url")
    failure.add_argument("--repo", default=".")
    failure.set_defaults(handler=record_failure)

    comparison = subparsers.add_parser(
        "record-comparison", help="record the final paired comparison"
    )
    comparison.add_argument("--trial-id", required=True)
    comparison.add_argument(
        "--result-status",
        required=True,
        choices=["improved", "regressed", "inconclusive"],
    )
    comparison.add_argument("--comparison-json", required=True)
    comparison.add_argument("--repo", default=".")
    comparison.set_defaults(handler=record_comparison)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except (TrialError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

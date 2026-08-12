"""Weave Model and JSON subprocess boundary for the TypeScript slide agent."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Literal

import weave
from pydantic import PrivateAttr

ROOT = Path(__file__).resolve().parents[1]
TSX = ROOT / "node_modules" / ".bin" / "tsx"
Variant = Literal["baseline", "improvement-1", "improvement-2"]
REQUIRED_OUTPUT_FIELDS = {
    "generation_success",
    "slides",
    "slides_text",
    "tool_calls",
    "final_text",
    "duration_ms",
    "conversation_id",
    "error",
}


def validate_agent_output(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Agent output must be a JSON object.")
    missing = REQUIRED_OUTPUT_FIELDS - value.keys()
    if missing:
        raise ValueError(f"Agent output is missing fields: {sorted(missing)}")
    if not isinstance(value["generation_success"], bool):
        raise ValueError("generation_success must be a boolean.")
    if not isinstance(value["slides_text"], str):
        raise ValueError("slides_text must be a string.")
    if not isinstance(value["tool_calls"], list):
        raise ValueError("tool_calls must be a list.")
    if not isinstance(value["conversation_id"], str):
        raise ValueError("conversation_id must be a string.")
    return value


async def run_agent_process(
    *,
    arxiv_id: str,
    paper_url: str,
    variant: Variant,
    timeout_seconds: int = 900,
) -> dict:
    if not TSX.is_file():
        raise RuntimeError(f"TypeScript runner was not found: {TSX}")
    run_id = str(uuid.uuid4())
    process = await asyncio.create_subprocess_exec(
        str(TSX),
        str(ROOT / "agent-run" / "eval.ts"),
        "--arxiv-id",
        arxiv_id,
        "--paper-url",
        paper_url,
        "--variant",
        variant,
        "--run-id",
        run_id,
        cwd=ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise TimeoutError(
            f"Slide agent timed out after {timeout_seconds} seconds."
        ) from None

    stdout_text = stdout.decode("utf-8", "replace")
    stderr_text = stderr.decode("utf-8", "replace")
    if process.returncode != 0:
        raise RuntimeError(
            f"Slide agent exited with code {process.returncode}.\n"
            f"stdout (tail): {stdout_text[-2000:] or '<empty>'}\n"
            f"stderr (tail): {stderr_text[-4000:] or '<empty>'}"
        )

    workspace_pattern = f"*-{variant}-{run_id}"
    workspaces = list((ROOT / "tmp" / "workspaces").glob(workspace_pattern))
    if len(workspaces) != 1:
        raise RuntimeError(
            "Could not identify the slide agent workspace for "
            f"run {run_id}: found {len(workspaces)} matches."
        )
    result_file = workspaces[0] / "evaluation-result.json"
    try:
        output = json.loads(result_file.read_text(encoding="utf-8"))
        return validate_agent_output(output)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(
            f"Slide agent returned an invalid result file {result_file}: {error}\n"
            f"stdout (tail): {stdout_text[-2000:] or '<empty>'}\n"
            f"stderr (tail): {stderr_text[-2000:] or '<empty>'}"
        ) from error


class SlideAgentModel(weave.Model):
    variant: Variant
    _timeout_seconds: int = PrivateAttr(default=900)

    def __init__(self, *, variant: Variant, timeout_seconds: int = 900):
        super().__init__(variant=variant)
        self._timeout_seconds = timeout_seconds

    @weave.op()
    async def predict(self, arxiv_id: str, paper_url: str) -> dict:
        return await run_agent_process(
            arxiv_id=arxiv_id,
            paper_url=paper_url,
            variant=self.variant,
            timeout_seconds=self._timeout_seconds,
        )

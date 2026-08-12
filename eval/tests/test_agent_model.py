import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_model import (  # noqa: E402
    run_agent_process,
    validate_agent_output,
)

RUN_ID = "a1b5e42a-ff4e-4c17-89b4-ad89fae6363e"


def valid_output():
    return {
        "generation_success": True,
        "slides": {"title": "Paper", "slides": []},
        "slides_text": "# Paper",
        "tool_calls": [],
        "final_text": "done",
        "duration_ms": 1,
        "conversation_id": "baseline:id",
        "error": None,
    }


class AgentModelTest(unittest.IsolatedAsyncioTestCase):
    def test_validate_rejects_missing_protocol_fields(self):
        with self.assertRaisesRegex(ValueError, "missing fields"):
            validate_agent_output({"generation_success": True})

    async def test_subprocess_success_reads_workspace_result_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = (
                root / "tmp" / "workspaces" / f"20260812061828-baseline-{RUN_ID}"
            )
            workspace.mkdir(parents=True)
            (workspace / "evaluation-result.json").write_text(
                json.dumps(valid_output()), encoding="utf-8"
            )
            process = AsyncMock()
            process.returncode = 0
            process.communicate.return_value = (b"weave: tracing enabled", b"log")
            with (
                patch("agent_model.ROOT", root),
                patch("agent_model.uuid.uuid4", return_value=RUN_ID),
                patch(
                    "agent_model.asyncio.create_subprocess_exec", return_value=process
                ) as create_process,
            ):
                result = await run_agent_process(
                    arxiv_id="1706.03762",
                    paper_url="https://arxiv.org/abs/1706.03762",
                    variant="baseline",
                    timeout_seconds=1,
                )
        self.assertEqual(result["conversation_id"], "baseline:id")
        process.communicate.assert_awaited_once_with()
        command = create_process.call_args.args
        self.assertEqual(command[2:4], ("--arxiv-id", "1706.03762"))
        self.assertIn(("--run-id", RUN_ID), tuple(zip(command, command[1:])))

    async def test_missing_result_file_reports_workspace_path_and_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = (
                root / "tmp" / "workspaces" / f"20260812061828-baseline-{RUN_ID}"
            )
            workspace.mkdir(parents=True)
            process = AsyncMock()
            process.returncode = 0
            process.communicate.return_value = (b"agent log", b"trace detail")
            with (
                patch("agent_model.ROOT", root),
                patch("agent_model.uuid.uuid4", return_value=RUN_ID),
                patch(
                    "agent_model.asyncio.create_subprocess_exec", return_value=process
                ),
                self.assertRaisesRegex(RuntimeError, "evaluation-result.json"),
            ):
                await run_agent_process(
                    arxiv_id="1706.03762",
                    paper_url="https://arxiv.org/abs/1706.03762",
                    variant="baseline",
                    timeout_seconds=1,
                )

    async def test_nonzero_exit_is_an_infrastructure_error(self):
        process = AsyncMock()
        process.returncode = 2
        process.communicate.return_value = (b"", b"failure")
        with patch("agent_model.asyncio.create_subprocess_exec", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "failure"):
                await run_agent_process(
                    arxiv_id="1706.03762",
                    paper_url="https://arxiv.org/abs/1706.03762",
                    variant="baseline",
                    timeout_seconds=1,
                )


if __name__ == "__main__":
    unittest.main()

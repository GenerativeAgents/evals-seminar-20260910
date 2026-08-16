/**
 * Weave Evaluationから起動される評価用エントリポイント。
 *
 *   tsx agent-run/eval.ts --arxiv-id <id> --paper-url <url> --variant <variant> --run-id <hex>
 *
 * 結果はrun workspace直下のevaluation-result.jsonへ書く。stdout/stderrはログ専用。
 * exit 0は「evaluation-result.jsonを書けた」ことを意味し、エージェントの振る舞いと
 * しての失敗(generate_pptx未成功)でもexit 0で終了する。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config as loadDotenv } from "dotenv";
import { createRunWorkspace } from "../agent/run-workspace";
import type { EvaluationTraceContext } from "../agent/weave-agent-tracing";
import { flushWeaveAgentTrace } from "../agent/weave-client";
import { runSlideAgent } from "./runner";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
loadDotenv({ path: path.join(ROOT, ".env"), quiet: true });

function parseArgs(argv: string[]): Record<string, string> {
  const args: Record<string, string> = {};
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (!flag?.startsWith("--") || value === undefined) {
      throw new Error(`Invalid arguments near: ${flag ?? "(end)"}`);
    }
    args[flag.slice(2)] = value;
  }
  return args;
}

function parseEvalContext(value: string): EvaluationTraceContext {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("--eval-context must be a JSON object.");
  }
  const context = parsed as Record<string, unknown>;
  for (const key of [
    "weave.eval.run_id",
    "weave.eval.predict_and_score_call_id",
  ]) {
    if (typeof context[key] !== "string" || context[key].length === 0) {
      throw new Error(`--eval-context is missing required string: ${key}`);
    }
  }
  return context as EvaluationTraceContext;
}

const args = parseArgs(process.argv.slice(2));
const arxivId = args["arxiv-id"];
const paperUrl = args["paper-url"];
const variant = args["variant"];
const runId = args["run-id"];
const evalContextJson = args["eval-context"];
if (!arxivId || !paperUrl || !variant || !runId || !evalContextJson) {
  console.error(
    "Usage: tsx agent-run/eval.ts --arxiv-id <id> --paper-url <url> --variant <variant> --run-id <hex> --eval-context <json>",
  );
  process.exit(1);
}
const evalContext = parseEvalContext(evalContextJson);

const workspaceDir = createRunWorkspace(variant, runId);
console.log(`[workspace] ${path.relative(ROOT, workspaceDir)}`);

const result = await runSlideAgent({
  arxivId,
  paperUrl,
  variant,
  workspaceDir,
  entrypoint: "eval",
  evalContext,
});

try {
  const resultFile = path.join(workspaceDir, "evaluation-result.json");
  fs.writeFileSync(
    resultFile,
    `${JSON.stringify(
      {
        generation_success: result.generationSuccess,
        slides: result.slides,
        slide_text: result.slideText,
        tool_calls: result.toolCalls,
        final_text: result.finalText,
        duration_ms: result.durationMs,
        conversation_id: result.conversationId,
        ...(result.failureReason === undefined
          ? {}
          : { failure_reason: result.failureReason }),
      },
      null,
      2,
    )}\n`,
  );
  console.log(`[saved] ${path.relative(ROOT, resultFile)}`);
} finally {
  try {
    await result.backend.close();
  } finally {
    await flushWeaveAgentTrace();
  }
}

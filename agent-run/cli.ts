import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config as loadDotenv } from "dotenv";
import { AGENT_MODEL } from "../agent/agent";
import { createRunWorkspace } from "../agent/run-workspace";
import { flushWeaveAgentTrace } from "../agent/weave-client";
import { runSlideAgent } from "./runner";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
loadDotenv({ path: path.join(ROOT, ".env"), quiet: true });

const [arxivId, variant = "baseline"] = process.argv.slice(2);
if (!arxivId) {
  console.error(
    "Usage: npm run agent -- <arxivId> [baseline|improvement-1|improvement-2]",
  );
  process.exit(1);
}

const workspaceDir = createRunWorkspace(variant, crypto.randomUUID());
console.log(`[workspace] ${path.relative(ROOT, workspaceDir)}`);

const result = await runSlideAgent({
  arxivId,
  paperUrl: `https://arxiv.org/abs/${arxivId}`,
  variant,
  workspaceDir,
  entrypoint: "cli",
});

try {
  if (!result.generationSuccess) {
    console.warn(`[warn] ${result.failureReason}`);
  }

  const resultFile = path.join(ROOT, "results", variant, `${arxivId}.json`);
  fs.mkdirSync(path.dirname(resultFile), { recursive: true });
  fs.writeFileSync(
    resultFile,
    `${JSON.stringify(
      {
        arxivId,
        variant,
        model: AGENT_MODEL,
        messages: result.messages,
        toolCalls: result.toolCalls,
        slides: result.slides,
        finalText: result.finalText,
        durationMs: result.durationMs,
        generatedAt: new Date().toISOString(),
      },
      null,
      2,
    )}\n`,
  );
  console.log(`\n[saved] results/${variant}/${arxivId}.json`);
} finally {
  try {
    await result.backend.close();
  } finally {
    await flushWeaveAgentTrace();
  }
}

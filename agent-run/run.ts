import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config as loadDotenv } from "dotenv";
import { isVariant } from "../variants";
import { runSlideAgent } from "./runner";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
loadDotenv({ path: path.join(ROOT, ".env"), quiet: true });

const [arxivId, rawVariant = "baseline"] = process.argv.slice(2);
if (!arxivId || !isVariant(rawVariant)) {
  console.error(
    "Usage: npm run agent -- <arxivId> [baseline|improvement-1|improvement-2]",
  );
  process.exit(1);
}

const result = await runSlideAgent({
  arxivId,
  paperUrl: `https://arxiv.org/abs/${arxivId}`,
  variant: rawVariant,
  entrypoint: "cli",
});
const resultFile = path.join(ROOT, "results", rawVariant, `${arxivId}.json`);
fs.mkdirSync(path.dirname(resultFile), { recursive: true });
fs.writeFileSync(
  resultFile,
  `${JSON.stringify(
    {
      arxivId: result.arxivId,
      variant: result.variant,
      model: result.model,
      messages: result.messages,
      toolCalls: result.toolCalls,
      slides: result.slides,
      finalText: result.finalText,
      durationMs: result.durationMs,
      conversationId: result.conversationId,
      generationSuccess: result.generationSuccess,
      error: result.error,
      generatedAt: new Date().toISOString(),
    },
    null,
    2,
  )}\n`,
);
console.log(`\n[saved] results/${rawVariant}/${arxivId}.json`);

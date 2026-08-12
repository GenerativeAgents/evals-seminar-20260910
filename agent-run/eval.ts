import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config as loadDotenv } from "dotenv";
import { z } from "zod";
import { VARIANTS } from "../variants";
import { runSlideAgent } from "./runner";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
loadDotenv({ path: path.join(ROOT, ".env"), quiet: true });

const requestSchema = z.object({
  arxivId: z.string().min(1),
  paperUrl: z.string().url(),
  variant: z.enum(VARIANTS),
  runId: z.uuid(),
});

function parseArgs(args: string[]): z.infer<typeof requestSchema> {
  const values: Record<string, string> = {};
  const names: Record<string, string> = {
    "--arxiv-id": "arxivId",
    "--paper-url": "paperUrl",
    "--variant": "variant",
    "--run-id": "runId",
  };
  for (let index = 0; index < args.length; index += 2) {
    const name = names[args[index]];
    const value = args[index + 1];
    if (!name || value === undefined || value.startsWith("--")) {
      throw new Error(
        "Usage: eval.ts --arxiv-id <id> --paper-url <url> " +
          "--variant <variant> --run-id <uuid>",
      );
    }
    if (name in values) {
      throw new Error(`Duplicate argument: ${args[index]}`);
    }
    values[name] = value;
  }
  return requestSchema.parse(values);
}

try {
  const input = parseArgs(process.argv.slice(2));
  const result = await runSlideAgent({
    arxivId: input.arxivId,
    paperUrl: input.paperUrl,
    variant: input.variant,
    runId: input.runId,
    entrypoint: "evaluation",
    requireWeaveTrace: true,
    log: (message) => console.log(message),
  });
  const outputPath = path.join(result.workspaceDir, "evaluation-result.json");
  fs.writeFileSync(
    outputPath,
    `${JSON.stringify({
      generation_success: result.generationSuccess,
      slides: result.slides,
      slides_text: result.slidesText,
      tool_calls: result.toolCalls,
      final_text: result.finalText,
      duration_ms: result.durationMs,
      conversation_id: result.conversationId,
      error: result.error,
    })}\n`,
    { flag: "wx" },
  );
  console.log(`[evaluation-result] ${outputPath}`);
} catch (error) {
  console.error(error instanceof Error ? error.stack : String(error));
  process.exitCode = 1;
}

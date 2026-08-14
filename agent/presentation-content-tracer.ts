import { spawn } from "node:child_process";
import path from "node:path";

const TRACE_SCRIPT = path.join(process.cwd(), "agent", "trace-presentation.py");
const TRACE_TIMEOUT_MS = 60_000;
const MAX_OUTPUT_BYTES = 32 * 1024 * 1024;

export interface PresentationContentPayload {
  projectPath: string;
  pptxBase64: string;
  slideData: {
    title: string;
    author?: string;
    slides: Array<{
      type: "title" | "section" | "content";
      title: string;
      subtitle?: string;
      bullets?: string[];
    }>;
  };
  metadata: {
    conversationId: string;
    variant: string;
    fileName: string;
    presentationTraceId: string;
  };
}

export interface PresentationContentResult {
  callId: string;
  traceId: string;
  contentTraceRef: string;
  contentTraceUrl: string;
  fileSizeBytes: number;
}

/** Run the Python Content API bridge required for document and HTML media. */
export async function tracePresentationContent(
  payload: PresentationContentPayload,
): Promise<PresentationContentResult> {
  const { stdout, stderr } = await new Promise<{
    stdout: string;
    stderr: string;
  }>((resolve, reject) => {
    const child = spawn(
      "uv",
      ["run", "--project", process.cwd(), "python", TRACE_SCRIPT],
      {
        stdio: ["pipe", "pipe", "pipe"],
        env: {
          ...process.env,
          WEAVE_PRINT_CALL_LINK: "false",
        },
      },
    );
    const stdoutChunks: Buffer[] = [];
    const stderrChunks: Buffer[] = [];
    let outputBytes = 0;
    let settled = false;
    const timeout = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill("SIGTERM");
      reject(
        new Error(
          `Presentation Content tracer timed out after ${TRACE_TIMEOUT_MS} ms.`,
        ),
      );
    }, TRACE_TIMEOUT_MS);

    const collect = (chunks: Buffer[], chunk: Buffer) => {
      outputBytes += chunk.length;
      if (outputBytes > MAX_OUTPUT_BYTES && !settled) {
        settled = true;
        clearTimeout(timeout);
        child.kill("SIGTERM");
        reject(new Error("Presentation Content tracer output was too large."));
        return;
      }
      chunks.push(chunk);
    };
    child.stdout.on("data", (chunk: Buffer) => collect(stdoutChunks, chunk));
    child.stderr.on("data", (chunk: Buffer) => collect(stderrChunks, chunk));
    child.stdin.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(error);
    });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(error);
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      const stdout = Buffer.concat(stdoutChunks).toString("utf8");
      const stderr = Buffer.concat(stderrChunks).toString("utf8");
      if (code !== 0) {
        reject(
          new Error(
            `Presentation Content tracer exited with code ${code}: ${stderr.trim()}`,
          ),
        );
        return;
      }
      resolve({ stdout, stderr });
    });

    child.stdin.end(JSON.stringify(payload));
  });

  if (stderr.trim() && process.env.PRESENTATION_TRACE_DEBUG === "1") {
    console.info(`[weave] Presentation Content tracer: ${stderr.trim()}`);
  }

  const lines = stdout.trim().split("\n");
  const lastLine = lines.at(-1);
  if (!lastLine) {
    throw new Error("Presentation Content tracer returned no result.");
  }
  return JSON.parse(lastLine) as PresentationContentResult;
}

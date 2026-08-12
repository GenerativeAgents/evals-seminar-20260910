import crypto from "node:crypto";
import type { Variant } from "../variants";
import { AGENT_MODEL, createSlideAgent } from "../agent/agent";
import { createRunWorkspace } from "../agent/run-workspace";
import { wrapAgentWithWeaveTracing } from "../agent/weave-agent-tracing";
import {
  flushWeaveAgentTrace,
  initWeaveAgentTrace,
} from "../agent/weave-client";

export interface AgentToolCall {
  name: string;
  args: unknown;
}

export interface SlideAgentError {
  code: "generate_pptx_not_called" | "generate_pptx_failed";
  message: string;
}

export interface SlideAgentRunResult {
  arxivId: string;
  paperUrl: string;
  variant: Variant;
  model: string;
  messages: [string, string];
  generationSuccess: boolean;
  slides: Record<string, unknown> | null;
  slidesText: string;
  toolCalls: AgentToolCall[];
  finalText: string;
  durationMs: number;
  conversationId: string;
  workspaceDir: string;
  error: SlideAgentError | null;
}

export interface RunSlideAgentOptions {
  arxivId: string;
  paperUrl: string;
  variant: Variant;
  runId?: string;
  entrypoint: "cli" | "evaluation";
  requireWeaveTrace?: boolean;
  log?: (message: string) => void;
}

function extractMessageText(message: any): string {
  const content = message?.content;
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .map((part) => (typeof part === "string" ? part : (part?.text ?? "")))
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function extractToolOutput(output: unknown): string {
  if (typeof output === "string") {
    return output;
  }
  if (output && typeof output === "object" && "content" in output) {
    return extractMessageText(output);
  }
  return JSON.stringify(output ?? null);
}

function parseGeneratePptxOutput(output: unknown): Record<string, unknown> {
  const text = extractToolOutput(output);
  const parsed = JSON.parse(text) as Record<string, unknown>;
  if (parsed.success !== true || !Array.isArray(parsed.slides)) {
    const detail =
      typeof parsed.error === "string" ? parsed.error : "generate_pptx failed";
    throw new Error(detail);
  }
  return {
    title: parsed.title,
    ...(parsed.author === undefined ? {} : { author: parsed.author }),
    slides: parsed.slides,
  };
}

export function slidesToText(data: Record<string, any>): string {
  const parts = [`# ${data.title ?? ""}`];
  for (const [index, slide] of (data.slides ?? []).entries()) {
    if (slide.type === "title") {
      continue;
    }
    parts.push(
      `## Slide ${index + 1} [${slide.type ?? ""}] ${slide.title ?? ""}`,
    );
    if (slide.subtitle) {
      parts.push(String(slide.subtitle));
    }
    for (const bullet of slide.bullets ?? []) {
      parts.push(`- ${bullet}`);
    }
  }
  return parts.join("\n");
}

export async function runSlideAgent(
  options: RunSlideAgentOptions,
): Promise<SlideAgentRunResult> {
  const log = options.log ?? console.log;
  const tracingActive = await initWeaveAgentTrace();
  if (options.requireWeaveTrace && !tracingActive) {
    throw new Error("Weave Agent Trace initialization is required for evaluation.");
  }

  const threadId = options.runId ?? crypto.randomUUID();
  const workspaceDir = createRunWorkspace(options.variant, threadId);
  const { agent: rawAgent, backend } = await createSlideAgent(workspaceDir);
  const agent = wrapAgentWithWeaveTracing(rawAgent, {
    agentName: "slide-generator",
    model: AGENT_MODEL,
    variant: options.variant,
    entrypoint: options.entrypoint,
    attributes: { arxiv_id: options.arxivId },
  });
  const config = {
    recursionLimit: 150,
    configurable: { thread_id: threadId },
  };
  const toolCalls: AgentToolCall[] = [];
  let slides: Record<string, unknown> | null = null;
  let generateError: string | null = null;

  async function runTurn(content: string): Promise<string> {
    log(`[user] ${content}`);
    const events = agent.streamEvents(
      { messages: [{ role: "user", content }] },
      { ...config, version: "v2" },
    );
    for await (const event of events) {
      if (event.event === "on_tool_start") {
        toolCalls.push({ name: event.name, args: event.data?.input ?? {} });
        log(`[tool] ${event.name}`);
      }
      if (event.event === "on_tool_end" && event.name === "generate_pptx") {
        try {
          slides = parseGeneratePptxOutput(
            event.data?.output ?? event.output ?? event.data,
          );
          generateError = null;
        } catch (error) {
          generateError = error instanceof Error ? error.message : String(error);
        }
      }
    }
    const state: any = await agent.getState(config);
    const text = extractMessageText(state.values.messages.at(-1));
    log(text);
    return text;
  }

  const request = `${options.paperUrl} この論文からスライドを作成してください。`;
  const approval = "OKです。この構成でスライドを生成してください。";
  const startedAtMs = Date.now();
  try {
    await runTurn(request);
    const finalText = await runTurn(approval);
    const error: SlideAgentError | null = slides
      ? null
      : generateError
        ? { code: "generate_pptx_failed", message: generateError }
        : {
            code: "generate_pptx_not_called",
            message: "The agent did not produce a valid generate_pptx result.",
          };
    return {
      arxivId: options.arxivId,
      paperUrl: options.paperUrl,
      variant: options.variant,
      model: AGENT_MODEL,
      messages: [request, approval],
      generationSuccess: slides !== null,
      slides,
      slidesText: slides ? slidesToText(slides) : "",
      toolCalls,
      finalText,
      durationMs: Date.now() - startedAtMs,
      conversationId: `${options.variant}:${threadId}`,
      workspaceDir,
      error,
    };
  } finally {
    try {
      await backend.close();
    } finally {
      await flushWeaveAgentTrace();
    }
  }
}

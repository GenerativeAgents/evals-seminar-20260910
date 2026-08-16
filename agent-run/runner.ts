import crypto from "node:crypto";
import { AGENT_MODEL, createSlideAgent } from "../agent/agent";
import {
  buildConversationId,
  type EvaluationTraceContext,
  wrapAgentWithWeaveTracing,
} from "../agent/weave-agent-tracing";
import { initWeaveAgentTrace } from "../agent/weave-client";

export interface SlideEntry {
  type: string;
  title?: string;
  subtitle?: string;
  bullets?: string[];
}

export interface SlideData {
  title: string;
  author?: string;
  slides: SlideEntry[];
}

export interface ToolCallRecord {
  name: string;
  args: unknown;
}

export interface RunSlideAgentOptions {
  arxivId: string;
  paperUrl: string;
  variant: string;
  workspaceDir: string;
  entrypoint?: "cli" | "eval";
  evalContext?: EvaluationTraceContext;
}

export interface RunSlideAgentResult {
  generationSuccess: boolean;
  slides: SlideData | null;
  slideText: string;
  toolCalls: ToolCallRecord[];
  finalText: string;
  durationMs: number;
  conversationId: string;
  messages: [string, string];
  failureReason?: string;
  /** 呼び出し元がfinallyでclose()する */
  backend: { close(): Promise<void> };
}

/**
 * スライドJSONを評価用のプレーンテキストへ変換する。
 *
 * タイトルスライドは著者・所属・発表年などの書誌メタデータであり、
 * 要約の主張として判定させないため評価対象から除外する。
 * indexはスキップ分も進む1始まり(旧eval/cases.pyのslides_to_textと同一仕様)。
 */
export function slidesToText(data: SlideData): string {
  const parts = [`# ${data.title}`];
  data.slides.forEach((slide, index) => {
    if (slide.type === "title") {
      return;
    }
    parts.push(`## Slide ${index + 1} [${slide.type}] ${slide.title ?? ""}`);
    if (slide.subtitle) {
      parts.push(slide.subtitle);
    }
    for (const bullet of slide.bullets ?? []) {
      parts.push(`- ${bullet}`);
    }
  });
  return parts.join("\n");
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

function toolOutputToText(output: unknown): string {
  if (typeof output === "string") {
    return output;
  }
  if (output && typeof output === "object" && "content" in output) {
    return extractMessageText(output);
  }
  return "";
}

/** generate_pptxの成功結果からスライドデータを取り出す(検証はツール側で完了済み)。 */
function parseGeneratePptxResult(output: unknown): SlideData | undefined {
  try {
    const parsed = JSON.parse(toolOutputToText(output));
    if (parsed?.success === true && Array.isArray(parsed.slides)) {
      return {
        title: parsed.title,
        author: parsed.author,
        slides: parsed.slides,
      };
    }
  } catch {
    // 不正なJSONは失敗結果として無視する
  }
  return undefined;
}

/**
 * 2ターンのスライド生成エージェントを1回実行する。
 * results/ には書き込まず、生成物はrun workspaceと返り値だけに残す。
 */
export async function runSlideAgent(
  options: RunSlideAgentOptions,
): Promise<RunSlideAgentResult> {
  await initWeaveAgentTrace();
  const { agent: rawAgent, backend } = await createSlideAgent(
    options.workspaceDir,
  );
  const threadId = crypto.randomUUID();
  const agent = wrapAgentWithWeaveTracing(rawAgent, {
    agentName: "slide-generator",
    model: AGENT_MODEL,
    variant: options.variant,
    entrypoint: options.entrypoint ?? "cli",
    attributes: {
      arxiv_id: options.arxivId,
      ...(options.evalContext ?? {}),
    },
  });
  const config = {
    recursionLimit: 150,
    configurable: { thread_id: threadId },
  };

  // サブエージェント内部を含む全ツール呼び出しを評価用に記録する
  const toolCalls: ToolCallRecord[] = [];
  let slides: SlideData | undefined;

  async function runTurn(content: string): Promise<string> {
    console.log(`\n[user] ${content}`);
    const events = agent.streamEvents(
      { messages: [{ role: "user", content }] },
      { ...config, version: "v2" },
    );
    for await (const event of events) {
      if (event.event === "on_tool_start") {
        toolCalls.push({ name: event.name, args: event.data?.input ?? {} });
        console.log(`[tool] ${event.name}`);
      }
      if (event.event === "on_tool_end" && event.name === "generate_pptx") {
        // 成功結果を最後勝ちで保持する(リトライで上書きされる)
        const parsed = parseGeneratePptxResult(event.data?.output);
        if (parsed) {
          slides = parsed;
        }
      }
    }
    const state: any = await agent.getState(config);
    const text = extractMessageText(state.values.messages.at(-1));
    console.log(text);
    return text;
  }

  try {
    const startedAtMs = Date.now();

    // ターン1: 論文URLを渡し、アウトライン提案まで進める
    const request = `${options.paperUrl} この論文からスライドを作成してください。`;
    await runTurn(request);

    // ターン2: 人間の確認を固定の承認メッセージで置き換え、generate_pptxまで進める
    const approval = "OKです。この構成でスライドを生成してください。";
    const finalText = await runTurn(approval);

    const generationSuccess = slides !== undefined;
    return {
      generationSuccess,
      slides: slides ?? null,
      slideText: slides ? slidesToText(slides) : "",
      toolCalls,
      finalText,
      durationMs: Date.now() - startedAtMs,
      conversationId: buildConversationId(options.variant, threadId),
      messages: [request, approval],
      ...(generationSuccess
        ? {}
        : {
            failureReason:
              "generate_pptxの成功結果が得られませんでした(ツールが未呼び出しか、スライド検証に失敗)。",
          }),
      backend,
    };
  } catch (error) {
    // 例外時は呼び出し元にbackendが渡らないため、ここでcloseする
    await backend.close().catch(() => {});
    throw error;
  }
}

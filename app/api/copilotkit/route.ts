import crypto from "node:crypto";
import {
  CopilotRuntime,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { LangChainAgentAdapter } from "langchain-copilotkit";
import { AGENT_MODEL, createSlideAgent } from "../../../agent/agent";
import { createRunWorkspace } from "../../../agent/run-workspace";
import { wrapAgentWithWeaveTracing } from "../../../agent/weave-agent-tracing";
import { initWeaveAgentTrace } from "../../../agent/weave-client";
import { type Variant, VARIANTS } from "../../variants";

await initWeaveAgentTrace();

interface ConversationAgent {
  streamEvents(input: any, options: any): AsyncIterable<any>;
  getState(config: any): Promise<any>;
}

function sanitizeThreadId(threadId: string): string {
  const sanitized = threadId
    .replace(/[^A-Za-z0-9-]/g, "-")
    .replace(/^-+|-+$/g, "");
  return sanitized || crypto.randomUUID();
}

async function buildConversationAgent(
  variant: Variant,
  runId: string,
): Promise<ConversationAgent> {
  const workspaceDir = createRunWorkspace(variant, runId);
  const { agent: rawAgent } = await createSlideAgent(workspaceDir);
  // backendはcloseしない(現行同様、devサーバーのプロセス終了に任せる)
  return wrapAgentWithWeaveTracing(rawAgent, {
    agentName: "slide-generator",
    model: AGENT_MODEL,
    variant,
    entrypoint: "ui",
  });
}

/**
 * conversation(thread_id)ごとにagent・backend・run workspaceを遅延生成し、
 * 同じconversationの複数ターンで再利用するディスパッチproxy。
 * LangChainAgentAdapterが呼ぶのはstreamEvents/getStateの2メソッドだけである。
 * Mapは無制限に成長するが、devサーバー用途の既知の制約として許容する。
 */
function createConversationDispatcher(variant: Variant): ConversationAgent {
  const conversations = new Map<string, Promise<ConversationAgent>>();

  function getConversationAgent(
    threadId: unknown,
  ): Promise<ConversationAgent> {
    if (typeof threadId !== "string" || threadId === "") {
      // thread_id欠落時はリクエスト単位のworkspaceへフォールバックする
      return buildConversationAgent(variant, crypto.randomUUID());
    }
    let agentPromise = conversations.get(threadId);
    if (!agentPromise) {
      agentPromise = buildConversationAgent(
        variant,
        sanitizeThreadId(threadId),
      );
      conversations.set(threadId, agentPromise);
    }
    return agentPromise;
  }

  return {
    async *streamEvents(input: any, options: any) {
      const agent = await getConversationAgent(
        options?.configurable?.thread_id,
      );
      yield* agent.streamEvents(input, options);
    },
    async getState(config: any) {
      const threadId = config?.configurable?.thread_id;
      const agentPromise =
        typeof threadId === "string"
          ? conversations.get(threadId)
          : undefined;
      if (!agentPromise) {
        // 未知のthreadにはinterrupt検査用の空stateを返す
        return { values: {}, tasks: [] };
      }
      const agent = await agentPromise;
      return agent.getState(config);
    },
  };
}

const agents: Record<string, LangChainAgentAdapter> = {};
for (const variant of VARIANTS) {
  agents[variant] = new LangChainAgentAdapter({
    agent: createConversationDispatcher(variant),
    stateKeys: ["files"],
  });
}

const runtime = new CopilotRuntime({ agents });

export const { handleRequest: POST } = copilotRuntimeNextJSAppRouterEndpoint({
  runtime,
  endpoint: "/api/copilotkit",
});

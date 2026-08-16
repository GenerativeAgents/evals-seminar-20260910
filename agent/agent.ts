import path from "node:path";
import type { CallbackManagerForLLMRun } from "@langchain/core/callbacks/manager";
import type { BaseMessage } from "@langchain/core/messages";
import type { ChatResult } from "@langchain/core/outputs";
import { MemorySaver } from "@langchain/langgraph-checkpoint";
import { ChatOpenRouter } from "@langchain/openrouter";
import { createDeepAgent, LocalShellBackend } from "deepagents";
import { createGeneratePptxTool } from "./generate-pptx-tool";
import { SYSTEM_PROMPT } from "./system-prompt";
import {
  createWeaveAgentTraceMiddleware,
  weaveTraceContextSchema,
} from "./weave-agent-tracing";

export const AGENT_MODEL = "deepseek/deepseek-v4-flash";

/** LangChainのnon-streaming経路でもOpenRouterのraw usage/costを保持する。 */
class CostTrackingChatOpenRouter extends ChatOpenRouter {
  override async _generate(
    messages: BaseMessage[],
    options: this["ParsedCallOptions"],
    runManager?: CallbackManagerForLLMRun,
  ): Promise<ChatResult> {
    const result = await super._generate(messages, options, runManager);
    const usage = result.llmOutput?.tokenUsage;
    if (usage && typeof usage === "object") {
      for (const generation of result.generations) {
        generation.message.response_metadata = {
          ...generation.message.response_metadata,
          usage,
        };
      }
    }
    return result;
  }
}

export async function createSlideAgent(workspaceDir: string) {
  const model = new CostTrackingChatOpenRouter({
    model: AGENT_MODEL,
  });

  const backend = await LocalShellBackend.create({
    rootDir: path.resolve(workspaceDir),
    virtualMode: true,
    inheritEnv: true,
  });

  const agent = createDeepAgent({
    model,
    name: "slide-generator",
    systemPrompt: SYSTEM_PROMPT,
    tools: [createGeneratePptxTool(backend)],
    middleware: [createWeaveAgentTraceMiddleware()],
    contextSchema: weaveTraceContextSchema,
    skills: ["./.agent/skills/"],
    memory: ["./AGENTS.md"],
    backend,
    checkpointer: new MemorySaver(),
  });

  return { agent, backend };
}

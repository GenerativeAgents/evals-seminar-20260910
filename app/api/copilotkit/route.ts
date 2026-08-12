import {
  CopilotRuntime,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { LangChainAgentAdapter } from "langchain-copilotkit";
import { ConversationAgentRouter } from "../../../agent/conversation-agent";
import { initWeaveAgentTrace } from "../../../agent/weave-client";
import { VARIANTS } from "../../../variants";

await initWeaveAgentTrace();

const agents: Record<string, LangChainAgentAdapter> = {};
for (const variant of VARIANTS) {
  agents[variant] = new LangChainAgentAdapter({
    agent: new ConversationAgentRouter(variant),
    stateKeys: ["files"],
  });
}

const runtime = new CopilotRuntime({ agents });

export const { handleRequest: POST } = copilotRuntimeNextJSAppRouterEndpoint({
  runtime,
  endpoint: "/api/copilotkit",
});

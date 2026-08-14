import path from "node:path";
import {
  CopilotRuntime,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { LangChainAgentAdapter } from "langchain-copilotkit";
import { AGENT_MODEL, createSlideAgent } from "../../../agent/agent";
import { wrapAgentWithWeaveTracing } from "../../../agent/weave-agent-tracing";
import { initWeaveAgentTrace } from "../../../agent/weave-client";
import { VARIANTS } from "../../variants";

await initWeaveAgentTrace();

const agents: Record<string, LangChainAgentAdapter> = {};
for (const variant of VARIANTS) {
  const workspaceDir = path.resolve(process.cwd(), "workspaces", variant);
  const { agent: rawAgent } = await createSlideAgent(workspaceDir);
  const agent = wrapAgentWithWeaveTracing(rawAgent, {
    agentName: "slide-generator",
    model: AGENT_MODEL,
    variant,
    entrypoint: "ui",
  });
  agents[variant] = new LangChainAgentAdapter({
    agent,
    stateKeys: ["files"],
  });
}

const runtime = new CopilotRuntime({ agents });

export const { handleRequest: POST } = copilotRuntimeNextJSAppRouterEndpoint({
  runtime,
  endpoint: "/api/copilotkit",
});

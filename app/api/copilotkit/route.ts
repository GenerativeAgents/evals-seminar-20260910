import path from "node:path";
import {
  CopilotRuntime,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { LangChainAgentAdapter } from "langchain-copilotkit";
import { createSlideAgent } from "../../../agent/agent";
import { VARIANTS } from "../../variants";

const agents: Record<string, LangChainAgentAdapter> = {};
for (const variant of VARIANTS) {
  const workspaceDir = path.resolve(process.cwd(), "workspaces", variant);
  const { agent } = await createSlideAgent(workspaceDir);
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

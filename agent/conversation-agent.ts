import crypto from "node:crypto";
import type { BackendProtocolV2 } from "deepagents";
import type { Variant } from "../variants";
import { AGENT_MODEL, createSlideAgent } from "./agent";
import { createRunWorkspace } from "./run-workspace";
import { wrapAgentWithWeaveTracing } from "./weave-agent-tracing";

interface StreamableAgent {
  streamEvents(input: any, options: any): AsyncIterable<any>;
  getState(config: any): Promise<any>;
}

interface ConversationResources {
  agent: StreamableAgent;
  backend: BackendProtocolV2;
  workspaceDir: string;
}

type ResourceFactory = (
  variant: Variant,
  threadId: string,
) => Promise<ConversationResources>;

async function defaultResourceFactory(
  variant: Variant,
  _threadId: string,
): Promise<ConversationResources> {
  const workspaceDir = createRunWorkspace(variant, crypto.randomUUID());
  const { agent: rawAgent, backend } = await createSlideAgent(workspaceDir);
  const agent = wrapAgentWithWeaveTracing(rawAgent, {
    agentName: "slide-generator",
    model: AGENT_MODEL,
    variant,
    entrypoint: "ui",
  });
  return { agent, backend, workspaceDir };
}

/** Keep one in-memory agent/backend/run workspace per CopilotKit conversation. */
export class ConversationAgentRouter {
  private readonly conversations = new Map<
    string,
    Promise<ConversationResources>
  >();

  constructor(
    private readonly variant: Variant,
    private readonly resourceFactory: ResourceFactory = defaultResourceFactory,
  ) {}

  private threadId(config: any): string {
    const value = config?.configurable?.thread_id;
    if (typeof value !== "string" || value.length === 0) {
      throw new Error("CopilotKit did not provide a thread_id.");
    }
    return value;
  }

  private getResources(threadId: string): Promise<ConversationResources> {
    const existing = this.conversations.get(threadId);
    if (existing) {
      return existing;
    }
    const created = this.resourceFactory(this.variant, threadId).catch((error) => {
      this.conversations.delete(threadId);
      throw error;
    });
    this.conversations.set(threadId, created);
    return created;
  }

  async *streamEvents(input: any, config: any): AsyncIterable<any> {
    const resources = await this.getResources(this.threadId(config));
    yield* resources.agent.streamEvents(input, config);
  }

  async getState(config: any): Promise<any> {
    const resources = await this.getResources(this.threadId(config));
    return resources.agent.getState(config);
  }
}

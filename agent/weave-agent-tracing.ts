import crypto from "node:crypto";
import type { AIMessage, BaseMessage } from "@langchain/core/messages";
import { createMiddleware } from "langchain";
import * as weave from "weave";
import type {
  Message as WeaveMessage,
  MessagePart as WeaveMessagePart,
  Turn,
  Usage as WeaveUsage,
} from "weave";
import { z } from "zod";
import { isWeaveAgentTraceActive } from "./weave-client";

const MAX_TRACE_TEXT_LENGTH = 50_000;

export interface WeaveTraceContext {
  turn: Turn;
  model: string;
}

export const weaveTraceContextSchema = z
  .object({
    weaveTrace: z
      .custom<WeaveTraceContext>(
        (value) =>
          typeof value === "object" && value !== null && "turn" in value,
      )
      .optional(),
  })
  .passthrough();

interface StreamableAgent {
  streamEvents(input: any, options: any): AsyncIterable<any>;
  getState?(config: any): Promise<any>;
}

interface TraceOptions {
  agentName: string;
  model: string;
  variant: string;
  entrypoint: "cli" | "ui" | "eval";
  attributes?: Record<string, string | number | boolean>;
}

/** Evaluation行とAgent Traceを対応付ける共通のconversation ID形式。 */
export function buildConversationId(variant: string, threadId: string): string {
  return `${variant}:${threadId}`;
}
function toError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

function truncate(text: string): string {
  if (text.length <= MAX_TRACE_TEXT_LENGTH) {
    return text;
  }
  return `${text.slice(0, MAX_TRACE_TEXT_LENGTH)}\n...[truncated by Agent Trace]`;
}

function safeJsonStringify(value: unknown): string {
  try {
    return truncate(
      JSON.stringify(value, (_key, item) =>
        typeof item === "bigint" ? item.toString() : item,
      ),
    );
  } catch {
    return truncate(String(value));
  }
}

function contentToText(content: unknown): string {
  if (typeof content === "string") {
    return truncate(content);
  }
  if (!Array.isArray(content)) {
    return content == null ? "" : safeJsonStringify(content);
  }

  const text = content
    .map((part) => {
      if (typeof part === "string") {
        return part;
      }
      if (!part || typeof part !== "object") {
        return "";
      }
      if ("text" in part && typeof part.text === "string") {
        return part.text;
      }
      if ("content" in part && typeof part.content === "string") {
        return part.content;
      }
      return "";
    })
    .filter(Boolean)
    .join("\n");

  return truncate(text);
}

function getMessageType(message: any): string {
  if (typeof message?.getType === "function") {
    return message.getType();
  }
  return message?.type ?? message?.role ?? "";
}

function getMessageRole(message: any): WeaveMessage["role"] | undefined {
  switch (getMessageType(message)) {
    case "human":
    case "user":
      return "user";
    case "ai":
    case "assistant":
      return "assistant";
    case "system":
      return "system";
    case "tool":
      return "tool";
    case "function":
      return "function";
    default:
      return undefined;
  }
}

function toToolCallParts(message: any): WeaveMessagePart[] {
  if (!Array.isArray(message?.tool_calls)) {
    return [];
  }

  return message.tool_calls.map((toolCall: any) => ({
    type: "tool_call" as const,
    toolCallId: toolCall.id ?? "",
    toolName: toolCall.name ?? "unknown",
    arguments: safeJsonStringify(toolCall.args ?? {}),
  }));
}

function toWeaveMessage(message: any): WeaveMessage | undefined {
  const role = getMessageRole(message);
  if (!role) {
    return undefined;
  }

  const content = contentToText(message?.content);
  const toolCallParts = role === "assistant" ? toToolCallParts(message) : [];
  if (toolCallParts.length > 0) {
    const parts: WeaveMessagePart[] = [];
    if (content) {
      parts.push({ type: "text", content });
    }
    parts.push(...toolCallParts);
    return { role, parts };
  }

  if (role === "tool") {
    return {
      role,
      content,
      toolCallId: message?.tool_call_id,
      toolName: message?.name,
    };
  }

  return { role, content };
}

function toWeaveMessages(messages: BaseMessage[]): WeaveMessage[] {
  return messages
    .map((message) => toWeaveMessage(message))
    .filter((message): message is WeaveMessage => message !== undefined);
}

function toWeaveUsage(response: AIMessage): WeaveUsage {
  const usage = response.usage_metadata;
  if (!usage) {
    return {};
  }

  return {
    inputTokens: usage.input_tokens,
    outputTokens: usage.output_tokens,
    reasoningTokens: usage.output_token_details?.reasoning,
    cacheReadInputTokens: usage.input_token_details?.cache_read,
    cacheCreationInputTokens: usage.input_token_details?.cache_creation,
  };
}

function toolResultToText(result: unknown): string {
  if (result && typeof result === "object" && "content" in result) {
    return contentToText(result.content);
  }
  return safeJsonStringify(result);
}

function extractLatestUserMessage(input: any): string {
  const messages = input?.messages;
  if (!Array.isArray(messages)) {
    return "";
  }

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (getMessageRole(messages[index]) === "user") {
      return contentToText(messages[index]?.content);
    }
  }
  return "";
}

function extractFinalAssistantMessage(state: any): WeaveMessage | undefined {
  const messages = state?.values?.messages;
  if (!Array.isArray(messages)) {
    return undefined;
  }

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (getMessageRole(messages[index]) === "assistant") {
      return toWeaveMessage(messages[index]);
    }
  }
  return undefined;
}

function getRuntimeTrace(request: any): WeaveTraceContext | undefined {
  return request.runtime?.context?.weaveTrace;
}

/** Trace main-agent model and tool calls. Subagent internals are intentionally excluded. */
export function createWeaveAgentTraceMiddleware() {
  return createMiddleware({
    name: "WeaveAgentTraceMiddleware",
    contextSchema: weaveTraceContextSchema,

    wrapModelCall: async (request, handler) => {
      const trace = getRuntimeTrace(request);
      if (!trace) {
        return handler(request);
      }

      return weave.runIsolated(async () => {
        const systemInstructions = contentToText(request.systemMessage?.content);
        const llm = trace.turn.startLLM({
          model: trace.model,
          providerName: "openrouter",
          systemInstructions: systemInstructions ? [systemInstructions] : [],
        });
        let caughtError: Error | undefined;

        try {
          const response = await handler(request);
          const responseMessage = toWeaveMessage(response);
          llm.record({
            inputMessages: toWeaveMessages(request.messages),
            outputMessages: responseMessage ? [responseMessage] : [],
            usage: toWeaveUsage(response),
            responseModel:
              typeof response.response_metadata?.model_name === "string"
                ? response.response_metadata.model_name
                : undefined,
          });
          return response;
        } catch (error) {
          caughtError = toError(error);
          throw error;
        } finally {
          llm.end(caughtError ? { error: caughtError } : undefined);
        }
      });
    },

    wrapToolCall: async (request, handler) => {
      const trace = getRuntimeTrace(request);
      if (!trace) {
        return handler(request);
      }

      const args = safeJsonStringify(request.toolCall.args ?? {});
      if (request.toolCall.name === "task") {
        const subagentName =
          typeof request.toolCall.args?.subagent_type === "string"
            ? request.toolCall.args.subagent_type
            : "subagent";
        const subagent = trace.turn.startSubagent({
          name: subagentName,
          model: trace.model,
        });
        let caughtError: Error | undefined;

        try {
          return await handler(request);
        } catch (error) {
          caughtError = toError(error);
          throw error;
        } finally {
          subagent.end(caughtError ? { error: caughtError } : undefined);
        }
      }

      const toolCall = trace.turn.startTool({
        name: request.toolCall.name,
        args,
        toolCallId: request.toolCall.id,
      });
      let caughtError: Error | undefined;

      try {
        const result = await handler(request);
        toolCall.result = toolResultToText(result);
        return result;
      } catch (error) {
        caughtError = toError(error);
        throw error;
      } finally {
        toolCall.end(caughtError ? { error: caughtError } : undefined);
      }
    },
  });
}

/**
 * Preserve the agent's streaming interface while managing one Weave Turn for
 * the complete lifetime of each stream invocation.
 */
export function wrapAgentWithWeaveTracing<TAgent extends StreamableAgent>(
  agent: TAgent,
  options: TraceOptions,
) {
  return {
    async *streamEvents(input: any, config: any = {}) {
      if (!isWeaveAgentTraceActive()) {
        yield* agent.streamEvents(input, config);
        return;
      }

      const threadId =
        typeof config.configurable?.thread_id === "string"
          ? config.configurable.thread_id
          : crypto.randomUUID();
      const userMessage = extractLatestUserMessage(input);
      const trace = weave.runIsolated(() => {
        const conversation = weave.startConversation({
          agentName: options.agentName,
          conversationId: buildConversationId(options.variant, threadId),
          model: options.model,
          attributes: {
            variant: options.variant,
            entrypoint: options.entrypoint,
            ...(options.attributes ?? {}),
          },
        });
        const turn = conversation.startTurn({
          agentName: options.agentName,
          model: options.model,
          userMessage,
        });
        return { conversation, turn };
      });

      let caughtError: Error | undefined;
      try {
        const currentContext =
          config.context && typeof config.context === "object"
            ? config.context
            : {};
        const events = agent.streamEvents(input, {
          ...config,
          context: {
            ...currentContext,
            weaveTrace: {
              turn: trace.turn,
              model: options.model,
            } satisfies WeaveTraceContext,
          },
        });

        for await (const event of events) {
          yield event;
        }

        if (agent.getState) {
          try {
            const state = await agent.getState({
              configurable: config.configurable,
            });
            const outputMessage = extractFinalAssistantMessage(state);
            if (outputMessage) {
              trace.turn.record({ outputMessages: [outputMessage] });
            }
          } catch (error) {
            console.warn(
              `[weave] Failed to record the Turn output: ${
                error instanceof Error ? error.message : String(error)
              }`,
            );
          }
        }
      } catch (error) {
        caughtError = toError(error);
        throw error;
      } finally {
        weave.runIsolated(() => {
          const endOptions = caughtError ? { error: caughtError } : undefined;
          trace.turn.end(endOptions);
          trace.conversation.end(endOptions);
        });
      }
    },

    async getState(config: any) {
      if (!agent.getState) {
        throw new Error("The wrapped agent does not implement getState().");
      }
      return agent.getState(config);
    },
  };
}

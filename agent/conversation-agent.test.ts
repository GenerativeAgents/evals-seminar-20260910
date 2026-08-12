import assert from "node:assert/strict";
import test from "node:test";
import { ConversationAgentRouter } from "./conversation-agent";

async function collect(iterable: AsyncIterable<any>): Promise<any[]> {
  const values = [];
  for await (const value of iterable) {
    values.push(value);
  }
  return values;
}

test("reuses resources within a thread and isolates different threads", async () => {
  const created: string[] = [];
  const router = new ConversationAgentRouter("baseline", async (_variant, threadId) => {
    created.push(threadId);
    return {
      workspaceDir: `/tmp/${threadId}`,
      backend: {} as any,
      agent: {
        async *streamEvents() {
          yield { threadId };
        },
        async getState() {
          return { threadId };
        },
      },
    };
  });

  const firstConfig = { configurable: { thread_id: "thread-1" } };
  assert.deepEqual(await collect(router.streamEvents({}, firstConfig)), [
    { threadId: "thread-1" },
  ]);
  assert.deepEqual(await router.getState(firstConfig), { threadId: "thread-1" });
  assert.deepEqual(
    await collect(
      router.streamEvents({}, { configurable: { thread_id: "thread-2" } }),
    ),
    [{ threadId: "thread-2" }],
  );
  assert.deepEqual(created, ["thread-1", "thread-2"]);
});

test("requires CopilotKit thread_id", async () => {
  const router = new ConversationAgentRouter("baseline", async () => {
    throw new Error("should not be called");
  });
  await assert.rejects(async () => collect(router.streamEvents({}, {})), /thread_id/);
});

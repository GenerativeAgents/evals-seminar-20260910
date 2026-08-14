import { createHash } from "node:crypto";
import { z } from "zod";
import { tracePresentationContent } from "../../../agent/presentation-content-tracer";
import {
  getWeaveProjectPath,
  recordPresentationContentTrace,
} from "../../../agent/weave-client";
import { VARIANTS } from "../../variants";

const MAX_PPTX_BASE64_LENGTH = 32 * 1024 * 1024;
const WANDB_API_KEY_PLACEHOLDER = "your_wandb_api_key_here";

interface PresentationTraceResponse {
  traced: true;
  linkedToAgentTrace: boolean;
  contentTraceRef: string;
  contentTraceUrl: string;
  presentationTraceId: string;
}

type PresentationTraceGlobal = typeof globalThis & {
  __evalsSeminarPresentationTraceJobs?: Map<
    string,
    Promise<PresentationTraceResponse>
  >;
};

const presentationTraceGlobal = globalThis as PresentationTraceGlobal;
const presentationTraceJobs =
  presentationTraceGlobal.__evalsSeminarPresentationTraceJobs ??
  new Map<string, Promise<PresentationTraceResponse>>();
presentationTraceGlobal.__evalsSeminarPresentationTraceJobs =
  presentationTraceJobs;

const slideSchema = z.object({
  type: z.enum(["title", "section", "content"]),
  title: z.string(),
  subtitle: z.string().optional(),
  bullets: z.array(z.string()).optional(),
});

const presentationTraceSchema = z.object({
  variant: z.enum(VARIANTS),
  threadId: z.string().trim().min(1).max(256),
  fileName: z.string().trim().min(1).max(255),
  pptxBase64: z.string().min(1).max(MAX_PPTX_BASE64_LENGTH),
  slideData: z.object({
    title: z.string(),
    author: z.string().optional(),
    slides: z.array(slideSchema),
  }),
});

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }

  const parsed = presentationTraceSchema.safeParse(body);
  if (!parsed.success) {
    return Response.json({ error: "invalid_presentation" }, { status: 400 });
  }

  const projectPath = getWeaveProjectPath();
  const apiKey = process.env.WANDB_API_KEY?.trim();
  if (!apiKey || apiKey === WANDB_API_KEY_PLACEHOLDER || !projectPath) {
    return Response.json({ error: "weave_tracing_disabled" }, { status: 503 });
  }

  const event = parsed.data;
  const conversationId = `${event.variant}:${event.threadId}`;
  const presentationTraceId = createHash("sha256")
    // Identify the logical deck, not the generated ZIP bytes. PPTX generators
    // may embed timestamps, so the same slides can otherwise hash differently
    // after a page refresh.
    .update(JSON.stringify({ fileName: event.fileName, data: event.slideData }))
    .digest("hex");
  const idempotencyKey = `${conversationId}:${presentationTraceId}`;

  let traceJob = presentationTraceJobs.get(idempotencyKey);
  if (!traceJob) {
    traceJob = (async (): Promise<PresentationTraceResponse> => {
      const content = await tracePresentationContent({
        projectPath,
        pptxBase64: event.pptxBase64,
        slideData: event.slideData,
        metadata: {
          conversationId,
          variant: event.variant,
          fileName: event.fileName,
          presentationTraceId,
        },
      });
      const tracedAt = new Date().toISOString();
      const linked = await recordPresentationContentTrace({
        conversationId,
        variant: event.variant,
        presentationTraceId,
        contentTraceRef: content.contentTraceRef,
        contentTraceUrl: content.contentTraceUrl,
        fileName: event.fileName,
        fileSizeBytes: content.fileSizeBytes,
        slideCount: event.slideData.slides.length,
        tracedAt,
      });

      return {
        traced: true,
        linkedToAgentTrace: linked,
        contentTraceRef: content.contentTraceRef,
        contentTraceUrl: content.contentTraceUrl,
        presentationTraceId,
      };
    })();
    presentationTraceJobs.set(idempotencyKey, traceJob);
  }

  try {
    return Response.json(await traceJob);
  } catch (error) {
    if (presentationTraceJobs.get(idempotencyKey) === traceJob) {
      presentationTraceJobs.delete(idempotencyKey);
    }
    console.error("[weave] Failed to trace presentation content:", error);
    return Response.json({ error: "presentation_trace_failed" }, { status: 500 });
  }
}

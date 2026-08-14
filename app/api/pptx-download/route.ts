import { z } from "zod";
import { recordPptxDownloadTrace } from "../../../agent/weave-client";
import { VARIANTS } from "../../variants";

const downloadEventSchema = z.object({
  variant: z.enum(VARIANTS),
  threadId: z.string().trim().min(1).max(256),
  downloadEventId: z.uuid(),
  fileName: z.string().trim().min(1).max(255),
  fileSizeBytes: z.number().int().nonnegative(),
  slideCount: z.number().int().nonnegative(),
  downloadedAt: z.iso.datetime(),
});

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }

  const parsed = downloadEventSchema.safeParse(body);
  if (!parsed.success) {
    return Response.json(
      { error: "invalid_download_event" },
      { status: 400 },
    );
  }

  const event = parsed.data;
  try {
    const traced = await recordPptxDownloadTrace({
      conversationId: `${event.variant}:${event.threadId}`,
      variant: event.variant,
      downloadEventId: event.downloadEventId,
      fileName: event.fileName,
      fileSizeBytes: event.fileSizeBytes,
      slideCount: event.slideCount,
      downloadedAt: event.downloadedAt,
    });

    if (!traced) {
      return Response.json({ error: "weave_tracing_disabled" }, { status: 503 });
    }

    return Response.json({ traced: true });
  } catch (error) {
    console.error("[weave] Failed to record PPTX download:", error);
    return Response.json({ error: "trace_write_failed" }, { status: 500 });
  }
}

import * as weave from "weave";

const WANDB_API_KEY_PLACEHOLDER = "your_wandb_api_key_here";
const WANDB_ENTITY_PLACEHOLDER = "your_wandb_entity_here";

type WeaveGlobalState = typeof globalThis & {
  __evalsSeminarWeaveInitPromise?: Promise<boolean>;
  __evalsSeminarWeaveActive?: boolean;
  __evalsSeminarWeaveDisabledNoticeShown?: boolean;
};

const weaveGlobal = globalThis as WeaveGlobalState;

function hasConfiguredApiKey(): boolean {
  const apiKey = process.env.WANDB_API_KEY?.trim();
  return Boolean(apiKey && apiKey !== WANDB_API_KEY_PLACEHOLDER);
}

function getWeaveProjectPath(): string | undefined {
  const entity = process.env.WANDB_ENTITY?.trim();
  const project = process.env.WANDB_PROJECT?.trim();
  if (
    !entity ||
    entity === WANDB_ENTITY_PLACEHOLDER ||
    !project
  ) {
    return undefined;
  }
  return `${entity}/${project}`;
}

/** Initialize Weave once per process. Returns false when tracing is disabled. */
export async function initWeaveAgentTrace(): Promise<boolean> {
  if (!hasConfiguredApiKey()) {
    if (!weaveGlobal.__evalsSeminarWeaveDisabledNoticeShown) {
      console.warn(
        "[weave] WANDB_API_KEY is not configured; Agent Trace is disabled.",
      );
      weaveGlobal.__evalsSeminarWeaveDisabledNoticeShown = true;
    }
    return false;
  }

  const projectPath = getWeaveProjectPath();
  if (!projectPath) {
    if (!weaveGlobal.__evalsSeminarWeaveDisabledNoticeShown) {
      console.warn(
        "[weave] WANDB_ENTITY and WANDB_PROJECT must be configured; Agent Trace is disabled.",
      );
      weaveGlobal.__evalsSeminarWeaveDisabledNoticeShown = true;
    }
    return false;
  }

  if (!weaveGlobal.__evalsSeminarWeaveInitPromise) {
    weaveGlobal.__evalsSeminarWeaveInitPromise = weave
      .init(projectPath)
      .then(() => {
        weaveGlobal.__evalsSeminarWeaveActive = true;
        return true;
      })
      .catch((error) => {
        weaveGlobal.__evalsSeminarWeaveInitPromise = undefined;
        weaveGlobal.__evalsSeminarWeaveActive = false;
        console.warn(
          `[weave] Agent Trace initialization failed; tracing is disabled: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
        return false;
      });
  }

  return weaveGlobal.__evalsSeminarWeaveInitPromise;
}

export function isWeaveAgentTraceActive(): boolean {
  return weaveGlobal.__evalsSeminarWeaveActive === true;
}

export interface PptxDownloadTraceEvent {
  conversationId: string;
  variant: string;
  downloadEventId: string;
  fileName: string;
  fileSizeBytes: number;
  slideCount: number;
  downloadedAt: string;
}

export interface PresentationContentTraceEvent {
  conversationId: string;
  variant: string;
  presentationTraceId: string;
  contentTraceRef: string;
  contentTraceUrl: string;
  fileName: string;
  fileSizeBytes: number;
  slideCount: number;
  tracedAt: string;
}

/** Link a rich-media presentation Call to the matching Agent Trace conversation. */
export async function recordPresentationContentTrace(
  event: PresentationContentTraceEvent,
): Promise<boolean> {
  if (!(await initWeaveAgentTrace())) {
    return false;
  }

  weave.runIsolated(() => {
    const attributes = {
      variant: event.variant,
      entrypoint: "ui",
      event_type: "presentation_content_trace",
      presentation_trace_id: event.presentationTraceId,
      presentation_traced: true,
      presentation_content_trace_ref: event.contentTraceRef,
      presentation_content_trace_url: event.contentTraceUrl,
      pptx_file_name: event.fileName,
      pptx_file_size_bytes: event.fileSizeBytes,
      pptx_slide_count: event.slideCount,
      presentation_traced_at: event.tracedAt,
    };
    const conversation = weave.startConversation({
      agentName: "slide-generator",
      conversationId: event.conversationId,
      attributes,
    });
    const turn = conversation.startTurn({
      agentName: "slide-generator",
    });
    const toolCall = turn.startTool({
      name: "trace_presentation",
      args: JSON.stringify({
        presentationTraceId: event.presentationTraceId,
        fileName: event.fileName,
        fileSizeBytes: event.fileSizeBytes,
        slideCount: event.slideCount,
      }),
    });

    toolCall.result = JSON.stringify({
      success: true,
      presentation_trace_id: event.presentationTraceId,
      content_trace_ref: event.contentTraceRef,
      content_trace_url: event.contentTraceUrl,
      formats: ["pptx", "html"],
    });
    toolCall.end();
    turn.end();
    conversation.end();
  });

  await weave.flushOTel();
  return true;
}

/** Append a PPTX download event to an existing Agent Trace conversation. */
export async function recordPptxDownloadTrace(
  event: PptxDownloadTraceEvent,
): Promise<boolean> {
  if (!(await initWeaveAgentTrace())) {
    return false;
  }

  weave.runIsolated(() => {
    const attributes = {
      variant: event.variant,
      entrypoint: "ui",
      event_type: "pptx_download",
      pptx_downloaded: true,
      download_status: "initiated",
      download_event_id: event.downloadEventId,
      pptx_file_name: event.fileName,
      pptx_file_size_bytes: event.fileSizeBytes,
      pptx_slide_count: event.slideCount,
      pptx_downloaded_at: event.downloadedAt,
    };
    const conversation = weave.startConversation({
      agentName: "slide-generator",
      conversationId: event.conversationId,
      attributes,
    });
    const turn = conversation.startTurn({
      agentName: "slide-generator",
    });
    const toolCall = turn.startTool({
      name: "download_pptx",
      args: JSON.stringify({
        fileName: event.fileName,
        fileSizeBytes: event.fileSizeBytes,
        slideCount: event.slideCount,
      }),
      toolCallId: event.downloadEventId,
    });

    toolCall.result = JSON.stringify({
      success: true,
      pptx_downloaded: true,
      download_status: "initiated",
      downloaded_at: event.downloadedAt,
    });
    toolCall.end();
    turn.end();
    conversation.end();
  });

  // The route should only acknowledge the event after it has reached Weave.
  await weave.flushOTel();
  return true;
}

/** Flush Agent Trace's OpenTelemetry exporter before a short-lived process exits. */
export async function flushWeaveAgentTrace(): Promise<void> {
  const initialized = await weaveGlobal.__evalsSeminarWeaveInitPromise;
  if (initialized) {
    try {
      await weave.flushOTel();
    } catch (error) {
      console.warn(
        `[weave] Failed to flush Agent Trace spans: ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    }
  }
}

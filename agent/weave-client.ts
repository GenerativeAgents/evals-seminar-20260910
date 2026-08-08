import * as weave from "weave";

const DEFAULT_WEAVE_PROJECT = "evals-seminar-20260910";
const WANDB_API_KEY_PLACEHOLDER = "your_wandb_api_key_here";

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

  if (!weaveGlobal.__evalsSeminarWeaveInitPromise) {
    const project =
      process.env.WEAVE_PROJECT?.trim() || DEFAULT_WEAVE_PROJECT;

    weaveGlobal.__evalsSeminarWeaveInitPromise = weave
      .init(project)
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

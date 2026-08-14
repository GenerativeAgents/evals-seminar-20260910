"use client";

import {
  CopilotKit,
  useCopilotChat,
  useThreads,
} from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";
import { useState } from "react";
import { SlideProvider } from "./components/slide-context";
import { SlidePreview } from "./components/slide-preview";
import { ToolCallRenderer } from "./components/tool-call-renderer";
import { VARIANTS, type Variant } from "./variants";

export default function Home() {
  const [variant, setVariant] = useState<Variant>("baseline");

  // key={variant}でプロバイダごと再マウントし、切り替え時に会話とプレビューをリセットする
  return (
    <CopilotKit key={variant} runtimeUrl="/api/copilotkit" agent={variant}>
      <Workbench variant={variant} onVariantChange={setVariant} />
    </CopilotKit>
  );
}

function Workbench({
  variant,
  onVariantChange,
}: {
  variant: Variant;
  onVariantChange: (variant: Variant) => void;
}) {
  const { isLoading } = useCopilotChat();
  const { threadId } = useThreads();

  return (
    <SlideProvider>
      <main className="flex h-screen bg-slate-50">
        {/* Left: Slide Preview */}
        <div className="flex w-1/2 flex-col border-r border-slate-200">
          <header className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
            <h1 className="text-base font-semibold tracking-tight text-slate-800">
              arXiv Slide Generator
            </h1>
            <label className="flex items-center gap-2 text-xs text-slate-500">
              ワークスペース
              <select
                value={variant}
                onChange={(e) => onVariantChange(e.target.value as Variant)}
                className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-700"
              >
                {VARIANTS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          </header>
          <div className="flex-1 overflow-auto p-4">
            <SlidePreview />
          </div>
        </div>

        {/* Right: Chat */}
        <div className="flex w-1/2 flex-col">
          <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-5 py-3">
            <span className="text-sm font-medium text-slate-600">Chat</span>
            {isLoading && (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
                <span className="inline-block size-1.5 animate-pulse rounded-full bg-indigo-500" />
                Processing
              </span>
            )}
          </header>
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <ToolCallRenderer variant={variant} threadId={threadId} />
            <CopilotChat
              labels={{
                title: "Slide Agent",
                initial:
                  "arXiv論文のURLを貼り付けてください。論文を分析してスライドを作成します。",
              }}
            />
          </div>
        </div>
      </main>
    </SlideProvider>
  );
}

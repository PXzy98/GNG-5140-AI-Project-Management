"use client";

import { useMemo, useState } from "react";

import { useAppContext } from "@/components/app-context";
import { SectionCard } from "@/components/ui";
import { getApiAdapter } from "@/lib/api";
import { ChatMessage } from "@/lib/types";

function detectIntent(text: string): "risk" | "drift" | "brief" {
  const normalized = text.toLowerCase();
  if (normalized.includes("risk") || normalized.includes("issue")) {
    return "risk";
  }
  if (normalized.includes("scope") || normalized.includes("drift")) {
    return "drift";
  }
  return "brief";
}

export default function ChatPage() {
  const { settings } = useAppContext();
  const api = useMemo(() => getApiAdapter(settings), [settings]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSend() {
    const text = draft.trim();
    if (!text || busy) {
      return;
    }
    setBusy(true);
    setDraft("");

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      text,
      timestamp: new Date().toISOString()
    };
    setMessages((prev) => [...prev, userMessage]);

    try {
      const intent = detectIntent(text);
      const art = await api.ingestText(text, "notes", settings.projectId);

      if (intent === "risk") {
        const risk = await api.identifyRisks([art.artifact_id], settings.projectId);
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            text: `Risk analysis complete. Found ${risk.risks.length} risk(s). Top: ${risk.risks[0]?.description || "none"}`,
            timestamp: new Date().toISOString()
          }
        ]);
      } else if (intent === "drift") {
        await api.setBaseline(settings.projectId, art.artifact_id);
        const drift = await api.checkDrift(settings.projectId, art.artifact_id);
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            text: `Scope check complete. Drift type: ${drift.drift_type}, alignment score: ${drift.overall_alignment_score.toFixed(2)}.`,
            timestamp: new Date().toISOString()
          }
        ]);
      } else {
        const brief = await api.generateBrief([settings.projectId]);
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            text: `Daily brief generated for ${brief.date}. Ranked tasks: ${brief.ranked_tasks.length}.`,
            timestamp: new Date().toISOString()
          }
        ]);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Request failed";
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: `Error: ${message}`,
          timestamp: new Date().toISOString()
        }
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="space-y-6">
      <SectionCard
        title="Chat Console"
        subtitle="Intent-based API orchestration for stage testing: risk, scope drift, and brief generation."
      >
        <div className="mb-4 grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700 md:grid-cols-3">
          <p><strong>Hint:</strong> Ask about risks.</p>
          <p><strong>Hint:</strong> Ask about scope drift.</p>
          <p><strong>Hint:</strong> Ask for daily priorities.</p>
        </div>

        <div className="space-y-3">
          <div className="h-[420px] overflow-auto rounded-xl border border-slate-200 bg-white p-3">
            {messages.length === 0 ? (
              <p className="text-sm text-slate-500">Start a conversation to test orchestration.</p>
            ) : (
              <div className="space-y-3">
                {messages.map((message) => (
                  <div
                    key={message.id}
                    className={`max-w-[85%] rounded-xl px-3 py-2 text-sm ${
                      message.role === "user"
                        ? "ml-auto bg-primary text-white"
                        : "bg-slate-100 text-slate-800"
                    }`}
                  >
                    {message.text}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="flex gap-2">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void onSend();
                }
              }}
              placeholder="Type: 'check risk', 'check scope drift', or 'generate today brief'..."
              className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-primary/20 focus:ring"
            />
            <button
              type="button"
              onClick={onSend}
              disabled={busy}
              className="rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy ? "Sending..." : "Send"}
            </button>
          </div>
        </div>
      </SectionCard>
    </main>
  );
}

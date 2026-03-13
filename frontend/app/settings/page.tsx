"use client";

import { useEffect, useState } from "react";

import { useAppContext } from "@/components/app-context";
import { SectionCard } from "@/components/ui";
import { defaultSettings } from "@/lib/settings";
import { LlmTier, PipelineSettings } from "@/lib/types";

const tiers: LlmTier[] = ["local", "small", "medium", "large"];

export default function SettingsPage() {
  const { settings, setSettings, resetSettings } = useAppContext();
  const [draft, setDraft] = useState<PipelineSettings>(settings);

  useEffect(() => {
    setDraft(settings);
  }, [settings]);

  function update<K extends keyof PipelineSettings>(key: K, value: PipelineSettings[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  function save() {
    setSettings(draft);
  }

  function loadHighQuality() {
    setDraft({
      ...draft,
      apiMode: "real",
      ingestion: { ...draft.ingestion, mode: "llm", tier: "large", llmMode: "full" },
      risk: { ...draft.risk, mode: "llm", tier: "large" },
      drift: { ...draft.drift, mode: "llm", tier: "large" },
      brief: { ...draft.brief, mode: "llm", tier: "large" },
      priority: { ...draft.priority, mode: "llm", tier: "large" }
    });
  }

  function loadFastDemo() {
    setDraft({
      ...draft,
      apiMode: "mock",
      ingestion: { ...draft.ingestion, mode: "llm", tier: "small", llmMode: "hybrid" },
      risk: { ...draft.risk, mode: "llm", tier: "small" },
      drift: { ...draft.drift, mode: "llm", tier: "small" },
      brief: { ...draft.brief, mode: "traditional", tier: "small" },
      priority: { ...draft.priority, mode: "traditional", tier: "small" }
    });
  }

  return (
    <main className="space-y-6">
      <SectionCard
        title="Model Settings"
        subtitle="Set each module to traditional or LLM mode, and choose LLM tiers independently."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block">
            <span className="mb-2 block text-sm font-semibold text-slate-700">API Mode</span>
            <select
              value={draft.apiMode}
              onChange={(e) => update("apiMode", e.target.value as PipelineSettings["apiMode"])}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            >
              <option value="mock">mock</option>
              <option value="real">real</option>
            </select>
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-semibold text-slate-700">API Base URL</span>
            <input
              value={draft.apiBaseUrl}
              onChange={(e) => update("apiBaseUrl", e.target.value)}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-semibold text-slate-700">Project ID</span>
            <input
              value={draft.projectId}
              onChange={(e) => update("projectId", e.target.value)}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            />
          </label>
        </div>
      </SectionCard>

      <SectionCard title="Per-Module Controls">
        <div className="grid gap-4 lg:grid-cols-2">
          <ModuleBox
            title="Ingestion (M1)"
            mode={draft.ingestion.mode}
            tier={draft.ingestion.tier}
            onModeChange={(mode) => setDraft((prev) => ({ ...prev, ingestion: { ...prev.ingestion, mode } }))}
            onTierChange={(tier) => setDraft((prev) => ({ ...prev, ingestion: { ...prev.ingestion, tier } }))}
            extra={
              <label className="mt-3 block">
                <span className="mb-2 block text-xs font-semibold uppercase tracking-wide text-slate-500">LLM Mode</span>
                <select
                  value={draft.ingestion.llmMode}
                  onChange={(e) =>
                    setDraft((prev) => ({
                      ...prev,
                      ingestion: { ...prev.ingestion, llmMode: e.target.value as "hybrid" | "full" }
                    }))
                  }
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                >
                  <option value="hybrid">hybrid</option>
                  <option value="full">full</option>
                </select>
              </label>
            }
          />
          <ModuleBox
            title="Risk Engine (M3)"
            mode={draft.risk.mode}
            tier={draft.risk.tier}
            onModeChange={(mode) => setDraft((prev) => ({ ...prev, risk: { ...prev.risk, mode } }))}
            onTierChange={(tier) => setDraft((prev) => ({ ...prev, risk: { ...prev.risk, tier } }))}
          />
          <ModuleBox
            title="Drift Detector (M5)"
            mode="llm"
            tier={draft.drift.tier}
            onModeChange={() => {}}
            onTierChange={(tier) => setDraft((prev) => ({ ...prev, drift: { ...prev.drift, tier } }))}
            modeLockedMessage="LLM-only in current backend implementation"
          />
          <ModuleBox
            title="Daily Brief (M4)"
            mode={draft.brief.mode}
            tier={draft.brief.tier}
            onModeChange={(mode) => setDraft((prev) => ({ ...prev, brief: { ...prev.brief, mode } }))}
            onTierChange={(tier) => setDraft((prev) => ({ ...prev, brief: { ...prev.brief, tier } }))}
          />
          <ModuleBox
            title="Priority Ranker (M2)"
            mode={draft.priority.mode}
            tier={draft.priority.tier}
            onModeChange={(mode) => setDraft((prev) => ({ ...prev, priority: { ...prev.priority, mode } }))}
            onTierChange={(tier) => setDraft((prev) => ({ ...prev, priority: { ...prev.priority, tier } }))}
          />
        </div>
      </SectionCard>

      <SectionCard title="Presets and Save">
        <div className="flex flex-wrap gap-3">
          <button type="button" onClick={loadFastDemo} className="rounded-xl bg-slate-200 px-4 py-2 text-sm font-semibold text-slate-800">
            Load Fast Demo
          </button>
          <button type="button" onClick={loadHighQuality} className="rounded-xl bg-amber-200 px-4 py-2 text-sm font-semibold text-amber-900">
            Load High Quality
          </button>
          <button type="button" onClick={() => setDraft(defaultSettings)} className="rounded-xl bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-700">
            Reset Draft
          </button>
          <button type="button" onClick={resetSettings} className="rounded-xl bg-red-100 px-4 py-2 text-sm font-semibold text-red-700">
            Reset Saved
          </button>
          <button type="button" onClick={save} className="rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-white">
            Save Settings
          </button>
        </div>
      </SectionCard>
    </main>
  );
}

function ModuleBox({
  title,
  mode,
  tier,
  onModeChange,
  onTierChange,
  modeLockedMessage,
  extra
}: {
  title: string;
  mode: "traditional" | "llm";
  tier: LlmTier;
  onModeChange: (mode: "traditional" | "llm") => void;
  onTierChange: (tier: LlmTier) => void;
  modeLockedMessage?: string;
  extra?: React.ReactNode;
}) {
  const modeDisabled = Boolean(modeLockedMessage);
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="mb-2 block text-xs font-semibold uppercase tracking-wide text-slate-500">Mode</span>
          <select
            value={mode}
            onChange={(e) => onModeChange(e.target.value as "traditional" | "llm")}
            disabled={modeDisabled}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:bg-slate-100"
          >
            <option value="traditional">traditional</option>
            <option value="llm">llm</option>
          </select>
          {modeLockedMessage ? <p className="mt-1 text-xs text-amber-700">{modeLockedMessage}</p> : null}
        </label>
        <label className="block">
          <span className="mb-2 block text-xs font-semibold uppercase tracking-wide text-slate-500">Tier</span>
          <select
            value={tier}
            onChange={(e) => onTierChange(e.target.value as LlmTier)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          >
            {tiers.map((t) => (
              <option value={t} key={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
      </div>
      {extra}
    </div>
  );
}

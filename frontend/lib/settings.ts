import { PipelineSettings } from "@/lib/types";

const SETTINGS_KEY = "work_pulse_demo_settings_v1";

export const defaultSettings: PipelineSettings = {
  apiMode: (process.env.NEXT_PUBLIC_DEMO_MODE as "real" | "mock") || "mock",
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000",
  projectId: "proj_demo_001",
  ingestion: { mode: "llm", tier: "large", llmMode: "hybrid" },
  risk: { mode: "llm", tier: "large" },
  drift: { mode: "llm", tier: "large" },
  brief: { mode: "llm", tier: "medium" },
  priority: { mode: "llm", tier: "medium" }
};

export function loadSettings(): PipelineSettings {
  if (typeof window === "undefined") {
    return defaultSettings;
  }
  try {
    const raw = window.localStorage.getItem(SETTINGS_KEY);
    if (!raw) {
      return defaultSettings;
    }
    const parsed = JSON.parse(raw) as Partial<PipelineSettings>;
    return {
      ...defaultSettings,
      ...parsed,
      ingestion: { ...defaultSettings.ingestion, ...parsed.ingestion },
      risk: { ...defaultSettings.risk, ...parsed.risk },
      drift: { ...defaultSettings.drift, ...parsed.drift },
      brief: { ...defaultSettings.brief, ...parsed.brief },
      priority: { ...defaultSettings.priority, ...parsed.priority }
    };
  } catch {
    return defaultSettings;
  }
}

export function saveSettings(settings: PipelineSettings): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

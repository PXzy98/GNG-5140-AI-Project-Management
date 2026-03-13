"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";

import { defaultSettings, loadSettings, saveSettings } from "@/lib/settings";
import { PipelineSettings } from "@/lib/types";

interface AppContextValue {
  settings: PipelineSettings;
  setSettings: (settings: PipelineSettings) => void;
  resetSettings: () => void;
}

const AppContext = createContext<AppContextValue | undefined>(undefined);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [settings, setSettingsState] = useState<PipelineSettings>(defaultSettings);

  useEffect(() => {
    setSettingsState(loadSettings());
  }, []);

  const setSettings = (next: PipelineSettings) => {
    setSettingsState(next);
    saveSettings(next);
  };

  const resetSettings = () => setSettings(defaultSettings);

  const value = useMemo(() => ({ settings, setSettings, resetSettings }), [settings]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppContext(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) {
    throw new Error("useAppContext must be used within AppProvider");
  }
  return value;
}

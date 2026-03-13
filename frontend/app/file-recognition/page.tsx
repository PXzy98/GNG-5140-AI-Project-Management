"use client";

import { useMemo, useState } from "react";

import { useAppContext } from "@/components/app-context";
import { JsonBlock, SectionCard } from "@/components/ui";
import { getApiAdapter } from "@/lib/api";
import { ArtifactResponse } from "@/lib/types";

export default function FileRecognitionPage() {
  const { settings } = useAppContext();
  const api = useMemo(() => getApiAdapter(settings), [settings]);
  const [file, setFile] = useState<File | null>(null);
  const [sourceType, setSourceType] = useState("other");
  const [busy, setBusy] = useState(false);
  const [artifact, setArtifact] = useState<ArtifactResponse | null>(null);
  const [error, setError] = useState("");

  async function onUpload() {
    if (!file) {
      setError("Please choose a file first.");
      return;
    }
    setBusy(true);
    setError("");
    setArtifact(null);
    try {
      const output = await api.ingestFile(file, sourceType, settings.projectId);
      setArtifact(output);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="space-y-6">
      <SectionCard
        title="File Recognition"
        subtitle="Upload artifacts to validate extraction, language detection, and task parsing behavior."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4">
            <span className="mb-2 block text-sm font-semibold text-slate-700">Choose file</span>
            <input
              type="file"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              className="block w-full text-sm"
            />
            {file ? <p className="mt-2 text-xs text-slate-600">Selected: {file.name}</p> : null}
          </label>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <label className="block">
              <span className="mb-2 block text-sm font-semibold text-slate-700">Source type</span>
              <select
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
              >
                <option value="other">other</option>
                <option value="meeting_minutes">meeting_minutes</option>
                <option value="email">email</option>
                <option value="notes">notes</option>
                <option value="status_report">status_report</option>
              </select>
            </label>
            <button
              type="button"
              onClick={onUpload}
              disabled={busy}
              className="mt-4 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy ? "Processing..." : "Run File Recognition"}
            </button>
            {error ? <p className="mt-3 text-sm font-semibold text-red-700">{error}</p> : null}
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Recognition Output">
        {artifact ? <JsonBlock data={artifact} /> : <p className="text-sm text-slate-500">No file result yet.</p>}
      </SectionCard>
    </main>
  );
}

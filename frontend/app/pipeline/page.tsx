"use client";

import { useMemo, useState } from "react";

import { useAppContext } from "@/components/app-context";
import { JsonBlock, Pill, SectionCard } from "@/components/ui";
import { runPipeline } from "@/lib/api";
import { PipelineRunResult } from "@/lib/types";

type StepState = "idle" | "running" | "done" | "failed";

const initialSteps: Record<string, StepState> = {
  baseline_ingest: "idle",
  baseline_set: "idle",
  update_ingest: "idle",
  risk: "idle",
  drift: "idle",
  brief: "idle"
};

export default function PipelinePage() {
  const { settings } = useAppContext();
  const [baselineText, setBaselineText] = useState(
    "Statement of Work: In scope includes migration planning, validation, and cutover support. Out of scope includes disaster recovery expansion."
  );
  const [updateText, setUpdateText] = useState(
    "Meeting note: Team requested adding a new DR environment and extra vendor integration deliverable by end of month."
  );
  const [steps, setSteps] = useState(initialSteps);
  const [result, setResult] = useState<PipelineRunResult | null>(null);
  const [error, setError] = useState<string>("");
  const [running, setRunning] = useState(false);

  const progress = useMemo(() => {
    const values = Object.values(steps);
    const done = values.filter((state) => state === "done").length;
    return Math.round((done / values.length) * 100);
  }, [steps]);

  async function onRunPipeline() {
    setRunning(true);
    setError("");
    setResult(null);
    setSteps({
      baseline_ingest: "running",
      baseline_set: "idle",
      update_ingest: "idle",
      risk: "idle",
      drift: "idle",
      brief: "idle"
    });

    try {
      const output = await runPipeline(settings, {
        baselineText,
        updateText,
        baselineSourceType: "status_report",
        updateSourceType: "meeting_minutes"
      });
      setSteps({
        baseline_ingest: "done",
        baseline_set: "done",
        update_ingest: "done",
        risk: "done",
        drift: "done",
        brief: "done"
      });
      setResult(output);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pipeline failed");
      const firstPending = Object.entries(steps).find(([, state]) => state !== "done")?.[0];
      setSteps((current) => {
        const copy = { ...current };
        if (firstPending) {
          copy[firstPending] = "failed";
        }
        return copy;
      });
    } finally {
      setRunning(false);
    }
  }

  return (
    <main className="space-y-6">
      <SectionCard title="Pipeline Runner" subtitle="Normal stage flow: baseline setup, update ingestion, risk detection, drift check, and daily brief generation.">
        <div className="grid gap-4 lg:grid-cols-2">
          <label className="block">
            <span className="mb-2 block text-sm font-semibold text-slate-700">Baseline text (SOW-style input)</span>
            <textarea
              value={baselineText}
              onChange={(e) => setBaselineText(e.target.value)}
              className="h-40 w-full rounded-xl border border-slate-300 bg-white p-3 text-sm outline-none ring-primary/20 focus:ring"
            />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-semibold text-slate-700">Update text (meeting/email input)</span>
            <textarea
              value={updateText}
              onChange={(e) => setUpdateText(e.target.value)}
              className="h-40 w-full rounded-xl border border-slate-300 bg-white p-3 text-sm outline-none ring-primary/20 focus:ring"
            />
          </label>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onRunPipeline}
            disabled={running}
            className="rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-white hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {running ? "Running..." : "Run Full Pipeline"}
          </button>
          <div className="h-2 w-52 overflow-hidden rounded-full bg-slate-200">
            <div className="h-full bg-accent transition-all" style={{ width: `${progress}%` }} />
          </div>
          <span className="text-sm font-medium text-slate-600">{progress}% complete</span>
        </div>
        {error ? <p className="mt-3 text-sm font-semibold text-red-700">Error: {error}</p> : null}
      </SectionCard>

      <SectionCard title="Step Status">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(steps).map(([key, state]) => (
            <div key={key} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{key.replace("_", " ")}</p>
              <div className="mt-2">
                <Pill
                  text={state.toUpperCase()}
                  tone={state === "done" ? "good" : state === "failed" ? "warn" : "neutral"}
                />
              </div>
            </div>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="Pipeline Output">
        {result ? <JsonBlock data={result} /> : <p className="text-sm text-slate-500">Run the pipeline to see results.</p>}
      </SectionCard>
    </main>
  );
}

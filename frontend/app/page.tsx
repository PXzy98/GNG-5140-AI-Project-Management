"use client";

import Link from "next/link";

import { SectionCard } from "@/components/ui";
import { useAppContext } from "@/components/app-context";

const cards = [
  {
    title: "Pipeline Runner",
    href: "/pipeline",
    summary: "Run baseline -> ingestion -> risk -> drift -> brief in one guided flow."
  },
  {
    title: "Chat Console",
    href: "/chat",
    summary: "Test dialogue orchestration and module routing by user intent."
  },
  {
    title: "File Recognition",
    href: "/file-recognition",
    summary: "Upload files and validate extraction, language detection, and task parsing."
  },
  {
    title: "Model Settings",
    href: "/settings",
    summary: "Configure each module as traditional or LLM with specific model tiers."
  }
];

export default function HomePage() {
  const { settings } = useAppContext();

  return (
    <main className="space-y-6">
      <SectionCard
        title="Stage Demo Overview"
        subtitle="Use this interface to test module pipeline behavior with either real backend APIs or mock simulation."
      >
        <div className="grid gap-4 md:grid-cols-2">
          {cards.map((card) => (
            <Link
              key={card.title}
              href={card.href}
              className="group rounded-xl border border-slate-200 bg-gradient-to-br from-white to-slate-50 p-4 transition hover:-translate-y-0.5 hover:border-primary hover:shadow-panel"
            >
              <h3 className="text-base font-semibold text-ink group-hover:text-primary">{card.title}</h3>
              <p className="mt-2 text-sm text-slate-600">{card.summary}</p>
            </Link>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="Current Active Profile">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Stat label="API Mode" value={settings.apiMode.toUpperCase()} />
          <Stat label="Project ID" value={settings.projectId} />
          <Stat label="Ingestion" value={`${settings.ingestion.mode}/${settings.ingestion.tier}`} />
          <Stat label="Risk" value={`${settings.risk.mode}/${settings.risk.tier}`} />
          <Stat label="Drift" value={`${settings.drift.mode}/${settings.drift.tier}`} />
          <Stat label="Brief" value={`${settings.brief.mode}/${settings.brief.tier}`} />
        </div>
      </SectionCard>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-semibold text-slate-800">{value}</p>
    </div>
  );
}

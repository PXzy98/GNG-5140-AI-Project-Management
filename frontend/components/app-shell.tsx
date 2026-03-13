"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAppContext } from "@/components/app-context";

const navItems = [
  { href: "/", label: "Overview" },
  { href: "/pipeline", label: "Pipeline Runner" },
  { href: "/chat", label: "Chat Console" },
  { href: "/file-recognition", label: "File Recognition" },
  { href: "/settings", label: "Model Settings" }
];

function navClass(active: boolean): string {
  return active
    ? "rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white"
    : "rounded-lg px-3 py-2 text-sm font-semibold text-ink/70 hover:bg-slate-200";
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { settings } = useAppContext();

  return (
    <div className="mx-auto min-h-screen max-w-7xl px-4 pb-10 pt-6 sm:px-8">
      <header className="mb-6 rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-panel backdrop-blur">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-primary">Work Pulse Demo</p>
            <h1 className="text-2xl font-bold text-ink">AI Project Pipeline Stage UI</h1>
          </div>
          <div className="rounded-xl bg-slate-100 px-3 py-2 text-xs font-medium text-slate-700">
            API Mode: <span className="font-bold text-primary">{settings.apiMode.toUpperCase()}</span>
            {" · "}
            Project: <span className="font-bold">{settings.projectId}</span>
          </div>
        </div>
        <nav className="flex flex-wrap gap-2">
          {navItems.map((item) => (
            <Link key={item.href} href={item.href} className={navClass(path === item.href)}>
              {item.label}
            </Link>
          ))}
        </nav>
      </header>
      {children}
    </div>
  );
}

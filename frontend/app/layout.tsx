import type { Metadata } from "next";
import { Space_Grotesk } from "next/font/google";

import { AppProvider } from "@/components/app-context";
import { AppShell } from "@/components/app-shell";

import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"]
});

export const metadata: Metadata = {
  title: "Work Pulse Stage Demo",
  description: "Pipeline-based stage UI for ingestion, chat orchestration, and model controls"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={spaceGrotesk.className}>
        <AppProvider>
          <AppShell>{children}</AppShell>
        </AppProvider>
      </body>
    </html>
  );
}

import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";

import { OperatorControl } from "@/components/OperatorControl";

import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "AegisOps",
  description:
    "An incident-response agent over real OpenTelemetry data that checks every claim it makes and asks before it acts.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7f9" },
    { media: "(prefers-color-scheme: dark)", color: "#0d1014" },
  ],
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} font-sans antialiased`}>
        <header className="border-b border-border bg-surface">
          <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
            <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
              <span aria-hidden className="grid size-7 place-items-center rounded-md bg-accent text-sm text-accent-text">
                A
              </span>
              AegisOps
            </Link>
            <nav className="ml-auto flex items-center gap-4 text-sm text-muted">
              <Link href="/" className="hover:text-text">
                Incidents
              </Link>
              <a href="https://github.com/lokeshbothra21/aegisops" className="hover:text-text">
                GitHub
              </a>
              <OperatorControl />
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6 sm:py-8">{children}</main>
        <footer className="mx-auto max-w-6xl px-4 pb-10 text-xs text-muted">
          Detection and evidence checks are code, not the model. Public runs stop at the proposal:
          nothing executes without a human.{" "}
          <a className="underline hover:text-text" href="https://github.com/lokeshbothra21/aegisops#safety-model">
            Safety model
          </a>
        </footer>
      </body>
    </html>
  );
}

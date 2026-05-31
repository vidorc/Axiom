import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

import { CommandMenuTrigger } from "@/components/CommandMenuTrigger";
import { CommandPalette } from "@/components/CommandPalette";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Axiom — workflows",
  description: "Durable workflow engine for go-to-market. The in-product console.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-canvas-soft text-ink">
        {/* App shell — a calm, Linear-style top bar (UI_SYSTEM.md §2). The mesh
            gradient is reserved for marketing scale, so in-product chrome stays
            ink + gray; depth comes from the hairline + backdrop blur. */}
        <header className="sticky top-0 z-50 h-16 border-b border-hairline bg-canvas/70 backdrop-blur-xl">
          <div className="mx-auto flex h-full max-w-6xl items-center gap-6 px-6">
            <Link
              href="/"
              className="flex items-center gap-2 text-[15px] font-semibold tracking-tight"
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 2 L22 21 H2 Z" fill="#171717" />
                <path d="M12 9 L17 19 H7 Z" fill="#ffffff" />
              </svg>
              Axiom
            </Link>
            <nav className="flex items-center gap-1 text-sm text-body">
              <Link
                href="/"
                className="rounded-full px-3 py-1.5 transition-colors hover:bg-canvas-soft-2 hover:text-ink"
              >
                Workflows
              </Link>
            </nav>
            <div className="ml-auto flex items-center gap-3">
              <CommandMenuTrigger />
            </div>
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-12">{children}</main>
        {/* The ⌘K command palette — mounted once at the shell so it's reachable
            from every route (UI_SYSTEM.md §6). */}
        <CommandPalette />
      </body>
    </html>
  );
}

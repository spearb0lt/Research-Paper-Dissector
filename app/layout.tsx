import type { Metadata, Viewport } from "next";

import { ConfigProvider } from "@/components/ConfigProvider";
import { SiteHeader } from "@/components/SiteHeader";

import "./globals.css";

export const metadata: Metadata = {
  title: "Dissect",
  description:
    "Upload a research paper and interrogate every figure, table and claim in it, with citations that point at the page.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Light and dark are both real designs here, so the browser chrome is told
  // about each rather than being left to guess from the background.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfaf9" },
    { media: "(prefers-color-scheme: dark)", color: "#161513" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen">
        <ConfigProvider>
          <SiteHeader />
          <main className="mx-auto w-full max-w-[1400px] px-4 pb-16 sm:px-6">
            {children}
          </main>
        </ConfigProvider>
      </body>
    </html>
  );
}

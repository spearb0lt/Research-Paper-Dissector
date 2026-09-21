"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { useConfig, useHasLlm } from "./ConfigProvider";
import { Badge, cx } from "./ui";

const THEME_KEY = "dissect.theme.v1";

function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | "system">("system");

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem(THEME_KEY);
    } catch {
      stored = null;
    }
    if (stored === "light" || stored === "dark") {
      setTheme(stored);
      document.documentElement.dataset.theme = stored;
    }
  }, []);

  function choose(next: "light" | "dark" | "system") {
    setTheme(next);
    try {
      if (next === "system") window.localStorage.removeItem(THEME_KEY);
      else window.localStorage.setItem(THEME_KEY, next);
    } catch {
      // A blocked store only means the choice is not remembered.
    }
    if (next === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = next;
  }

  const order: ("light" | "dark" | "system")[] = ["light", "dark", "system"];
  const glyphs = { light: "☀", dark: "☾", system: "◐" };

  return (
    <button
      onClick={() => choose(order[(order.indexOf(theme) + 1) % order.length])}
      title={`Theme: ${theme}. Click to change.`}
      className="rounded-lg px-2 py-1.5 text-sm text-ink-muted hover:bg-raised"
      aria-label={`Theme: ${theme}`}
    >
      {glyphs[theme]}
    </button>
  );
}

const NAV = [
  { href: "/", label: "Library" },
  { href: "/ask", label: "Ask across papers" },
  { href: "/settings", label: "Settings" },
];

export function SiteHeader() {
  const pathname = usePathname();
  const { config } = useConfig();
  const hasLlm = useHasLlm();

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-canvas/85 backdrop-blur">
      <div className="mx-auto flex w-full max-w-[1400px] items-center gap-4 px-4 py-2.5 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold text-ink">
          <span className="grid h-6 w-6 place-items-center rounded-md bg-accent text-[13px] text-white">
            D
          </span>
          <span className="hidden sm:inline">{config?.app_name ?? "Dissect"}</span>
        </Link>

        <nav className="flex items-center gap-0.5">
          {NAV.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cx(
                  "rounded-lg px-2.5 py-1.5 text-sm transition-colors",
                  active ? "bg-raised font-medium text-ink" : "text-ink-muted hover:bg-raised",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          {/* The two facts that change what the app can do, stated in the
              chrome so nobody has to discover them by hitting a wall. */}
          {config ? (
            hasLlm ? (
              <Badge tone="good" title="A language model is configured, so written answers are available.">
                LLM ready
              </Badge>
            ) : (
              <Link href="/settings" title="No model configured. Retrieval still works fully.">
                <Badge tone="warn">Retrieval only</Badge>
              </Link>
            )
          ) : null}
          {config && !config.storage.persistent ? (
            <Badge tone="warn" title={config.storage.reason}>
              Temporary storage
            </Badge>
          ) : null}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

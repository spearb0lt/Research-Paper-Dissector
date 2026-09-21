"use client";

/**
 * Keyboard navigation, and the sheet that tells you it exists.
 *
 * Two rules everything here obeys.
 *
 * A shortcut never fires while you are typing. Every handler checks the focused
 * element first, because a reader who types "j" into the search box and watches
 * the page jump will not use the search box again.
 *
 * A shortcut is only bound where it does something. Binding `j` globally and
 * having it do nothing on most screens teaches people that the keyboard does
 * not work here, which is worse than not binding it.
 */

import { useCallback, useEffect, useState } from "react";

import { Badge, Card, cx } from "./ui";

/** Whether the keystroke should be ignored because the user is typing. */
export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    target.isContentEditable
  );
}

export interface Shortcut {
  keys: string[];
  label: string;
  /** Returns true when it handled the key, so the default is prevented. */
  run: (event: KeyboardEvent) => boolean | void;
}

export function useShortcuts(shortcuts: Shortcut[], enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    function onKey(event: KeyboardEvent) {
      if (isTyping(event.target)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      for (const shortcut of shortcuts) {
        if (shortcut.keys.includes(event.key)) {
          if (shortcut.run(event) !== false) event.preventDefault();
          return;
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shortcuts, enabled]);
}

export function Key({ children }: { children: string }) {
  return (
    <kbd className="rounded border border-line bg-raised px-1.5 py-0.5 font-mono text-[11px] text-ink-muted">
      {children}
    </kbd>
  );
}

/**
 * The help sheet, opened with `?` and closed with escape.
 *
 * Takes the same shortcut list the screen bound, so the sheet cannot drift
 * out of step with what the keys actually do.
 */
export function ShortcutHelp({ shortcuts }: { shortcuts: Shortcut[] }) {
  const [open, setOpen] = useState(false);

  const toggle = useCallback((event: KeyboardEvent) => {
    if (isTyping(event.target)) return;
    if (event.key === "?") {
      event.preventDefault();
      setOpen((current) => !current);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }, []);

  useEffect(() => {
    window.addEventListener("keydown", toggle);
    return () => window.removeEventListener("keydown", toggle);
  }, [toggle]);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        title="Keyboard shortcuts"
        className="fixed bottom-4 right-4 z-40 rounded-full border border-line bg-surface px-2.5 py-1.5 text-[11px] text-ink-faint shadow-sm hover:text-ink no-print"
      >
        <Key>?</Key> keys
      </button>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-ink/20 p-4 backdrop-blur-sm no-print"
      onClick={() => setOpen(false)}
    >
      <Card
        className="w-full max-w-md p-4"
        // Clicking inside must not dismiss, or selecting text closes the sheet.
      >
        <div
          onClick={(event) => event.stopPropagation()}
          role="dialog"
          aria-label="Keyboard shortcuts"
        >
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink">Keyboard</h2>
            <Badge>press ? or escape to close</Badge>
          </div>
          <ul className="space-y-1.5">
            {shortcuts.map((shortcut) => (
              <li
                key={shortcut.label}
                className="flex items-center justify-between gap-4 text-[13px]"
              >
                <span className="text-ink-muted">{shortcut.label}</span>
                <span className="flex shrink-0 gap-1">
                  {shortcut.keys.map((key) => (
                    <Key key={key}>{key === " " ? "space" : key}</Key>
                  ))}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 border-t border-line pt-2 text-[11px] text-ink-faint">
            Shortcuts are ignored while you are typing in a field.
          </p>
        </div>
      </Card>
    </div>
  );
}

/**
 * Scroll an element into view and flash it, for list navigation.
 *
 * The flash matters: on a long list, scrolling to an item without marking it
 * leaves the reader hunting for which one moved.
 */
export function focusListItem(id: string) {
  const node = document.getElementById(id);
  if (!node) return;
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  node.classList.add("ring-2", "ring-accent");
  window.setTimeout(() => node.classList.remove("ring-2", "ring-accent"), 1200);
}

export { cx };

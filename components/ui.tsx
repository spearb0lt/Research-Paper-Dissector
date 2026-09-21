"use client";

/**
 * The shared primitives. Small on purpose: a component earns its place here
 * only when at least two screens need it, so that the screens stay readable
 * rather than becoming assemblies of abstractions.
 */

import { forwardRef, type ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ layout

export function Card({
  children,
  className,
  as: Tag = "div",
  id,
}: {
  children: ReactNode;
  className?: string;
  as?: "div" | "section" | "article" | "li";
  // Several views scroll a card into view by id, so it has to be settable.
  id?: string;
}) {
  return (
    <Tag id={id} className={cx("rounded-xl border border-line bg-surface", className)}>
      {children}
    </Tag>
  );
}

export function SectionTitle({
  children,
  action,
}: {
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 mb-3">
      <h2 className="text-sm font-semibold tracking-wide uppercase text-ink-faint">
        {children}
      </h2>
      {action}
    </div>
  );
}

// ----------------------------------------------------------------- controls

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  disabled?: boolean;
  title?: string;
  className?: string;
};

export function Button({
  children,
  onClick,
  type = "button",
  variant = "secondary",
  size = "md",
  disabled,
  title,
  className,
}: ButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:opacity-45 disabled:cursor-not-allowed";
  const sizes = { sm: "px-2.5 py-1 text-xs", md: "px-3.5 py-2 text-sm" };
  const variants = {
    primary: "bg-accent text-white hover:opacity-90",
    secondary: "border border-line bg-surface text-ink hover:bg-raised",
    ghost: "text-ink-muted hover:bg-raised hover:text-ink",
    danger: "border border-line text-danger hover:bg-danger-soft",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cx(base, sizes[size], variants[variant], className)}
    >
      {children}
    </button>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
  reason,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
  reason?: string;
}) {
  return (
    <label
      className={cx(
        "flex items-start gap-2.5 cursor-pointer select-none",
        disabled && "cursor-not-allowed opacity-60",
      )}
      title={disabled ? reason : undefined}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 accent-accent"
      />
      <span className="min-w-0">
        <span className="block text-sm text-ink">{label}</span>
        {/* A disabled control always says why, rather than leaving the reader
            to guess whether it is broken or unavailable. */}
        {(disabled && reason) || hint ? (
          <span className="block text-xs text-ink-faint mt-0.5">
            {disabled && reason ? reason : hint}
          </span>
        ) : null}
      </span>
    </label>
  );
}

export function Select({
  value,
  onChange,
  options,
  label,
  disabled,
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  options: { value: string; label: string; disabled?: boolean }[];
  label?: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <label className={cx("block", className)}>
      {label ? (
        <span className="block text-xs font-medium text-ink-faint mb-1">{label}</span>
      ) : null}
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm text-ink disabled:opacity-50"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export const TextInput = forwardRef<
  HTMLInputElement,
  {
    value: string;
    onChange: (next: string) => void;
    placeholder?: string;
    label?: string;
    type?: "text" | "password" | "search";
    onEnter?: () => void;
    className?: string;
    autoFocus?: boolean;
  }
>(function TextInput(
  { value, onChange, placeholder, label, type = "text", onEnter, className, autoFocus },
  ref,
) {
  return (
    <label className={cx("block", className)}>
      {label ? (
        <span className="block text-xs font-medium text-ink-faint mb-1">{label}</span>
      ) : null}
      <input
        ref={ref}
        type={type}
        value={value}
        autoFocus={autoFocus}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && onEnter) {
            event.preventDefault();
            onEnter();
          }
        }}
        className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-faint"
      />
    </label>
  );
});

// -------------------------------------------------------------- indicators

export function Badge({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "accent" | "warn" | "danger" | "good";
  title?: string;
}) {
  const tones = {
    neutral: "bg-raised text-ink-muted",
    accent: "bg-accent-soft text-accent-ink",
    warn: "bg-warn-soft text-warn",
    danger: "bg-danger-soft text-danger",
    good: "bg-good-soft text-good",
  };
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

const KIND_TONES: Record<string, string> = {
  figure: "text-kind-figure",
  table: "text-kind-table",
  formula: "text-kind-formula",
  reference: "text-kind-reference",
};

export function KindDot({ kind }: { kind: string }) {
  return (
    <span
      aria-hidden
      className={cx("text-[10px] leading-none", KIND_TONES[kind] ?? "text-kind-text")}
    >
      ●
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-muted">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label}
    </span>
  );
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const percent = Math.max(0, Math.min(100, Math.round(value * 100)));
  return (
    <div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-raised"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full bg-accent transition-all duration-300"
          style={{ width: `${percent}%` }}
        />
      </div>
      {label ? <p className="mt-1.5 text-xs text-ink-faint">{label}</p> : null}
    </div>
  );
}

// ------------------------------------------------------------------ states

export function Notice({
  tone = "neutral",
  title,
  children,
}: {
  tone?: "neutral" | "warn" | "danger" | "good" | "accent";
  title?: string;
  children?: ReactNode;
}) {
  const tones = {
    neutral: "border-line bg-raised text-ink-muted",
    accent: "border-accent/30 bg-accent-soft text-accent-ink",
    warn: "border-warn/30 bg-warn-soft text-warn",
    danger: "border-danger/30 bg-danger-soft text-danger",
    good: "border-good/30 bg-good-soft text-good",
  };
  return (
    <div className={cx("rounded-lg border px-3 py-2.5 text-sm", tones[tone])}>
      {title ? <p className="font-medium">{title}</p> : null}
      {children ? <div className={cx(title && "mt-1", "text-[13px]")}>{children}</div> : null}
    </div>
  );
}

/** An error surface that always shows the backend's hint, which tells the
 *  reader what to do next rather than only what went wrong. */
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  const hint =
    typeof error === "object" && error && "hint" in error
      ? String((error as { hint?: string }).hint ?? "")
      : "";
  return (
    <Notice tone="danger" title={message}>
      {hint || null}
    </Notice>
  );
}

export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children ? <div className="mt-1.5 text-sm text-ink-faint">{children}</div> : null}
    </div>
  );
}

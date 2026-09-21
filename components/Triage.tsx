"use client";

/**
 * Read state and a one line verdict.
 *
 * A library of fifty papers with no state becomes a junk drawer within a
 * month. The verdict is free text rather than a rating on purpose: the useful
 * thing to remember about a paper is the sentence you would say to a
 * colleague, and no number carries that.
 */

import { useEffect, useState } from "react";

import { setTriage } from "@/lib/api";
import type { Paper, Quality, ReadState } from "@/lib/types";

import { Badge, Button, Card, Notice, SectionTitle, TextInput, cx } from "./ui";

const STATES: { id: ReadState; label: string; tone: string }[] = [
  { id: "unread", label: "Unread", tone: "border-line text-ink-muted" },
  { id: "reading", label: "Reading", tone: "border-warn/40 bg-warn-soft text-warn" },
  { id: "read", label: "Read", tone: "border-good/40 bg-good-soft text-good" },
  { id: "rejected", label: "Rejected", tone: "border-danger/40 bg-danger-soft text-danger" },
];

export function ReadStateBadge({ state }: { state: ReadState }) {
  if (state === "unread") return null;
  const tone =
    state === "read" ? "good" : state === "rejected" ? "danger" : "warn";
  return <Badge tone={tone as "good" | "danger" | "warn"}>{state}</Badge>;
}

export function TriagePanel({
  paper,
  onChange,
}: {
  paper: Paper;
  onChange?: (paper: Paper) => void;
}) {
  const [state, setState] = useState<ReadState>(paper.read_state);
  const [verdict, setVerdict] = useState(paper.verdict ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setState(paper.read_state);
    setVerdict(paper.verdict ?? "");
  }, [paper.id, paper.read_state, paper.verdict]);

  async function save(next: { read_state?: ReadState; verdict?: string }) {
    setSaving(true);
    setSaved(false);
    try {
      const updated = await setTriage(paper.id, next);
      onChange?.(updated);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1600);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="p-3">
      <SectionTitle>Your verdict</SectionTitle>
      <div className="mb-2.5 flex flex-wrap gap-1.5">
        {STATES.map((entry) => (
          <button
            key={entry.id}
            disabled={saving}
            onClick={() => {
              setState(entry.id);
              void save({ read_state: entry.id });
            }}
            className={cx(
              "rounded-lg border px-2.5 py-1 text-xs transition-colors disabled:opacity-50",
              state === entry.id ? entry.tone : "border-line text-ink-faint hover:bg-raised",
            )}
          >
            {entry.label}
          </button>
        ))}
      </div>
      <TextInput
        value={verdict}
        onChange={setVerdict}
        onEnter={() => save({ verdict })}
        placeholder="The one line you would tell a colleague"
      />
      <div className="mt-2 flex items-center gap-2">
        <Button
          size="sm"
          onClick={() => save({ verdict })}
          disabled={saving || verdict === (paper.verdict ?? "")}
        >
          Save
        </Button>
        {saved ? <span className="text-[11px] text-good">Saved</span> : null}
      </div>
    </Card>
  );
}

export function QualityPanel({ quality }: { quality: Quality | null }) {
  if (!quality) return null;
  const tone =
    quality.grade === "good" ? "good" : quality.grade === "fair" ? "warn" : "danger";

  return (
    <Card className="p-3">
      <SectionTitle>
        <span className="inline-flex items-center gap-2">
          Extraction quality
          <Badge tone={tone as "good" | "warn" | "danger"}>
            {quality.grade} · {Math.round(quality.score * 100)}%
          </Badge>
        </span>
      </SectionTitle>

      {quality.concerns.length === 0 ? (
        <p className="text-[12px] text-ink-faint">
          Nothing looks wrong with this parse. Tables were read from ruling
          lines, figures were extracted rather than cropped, and every page had
          a text layer.
        </p>
      ) : (
        <ul className="space-y-2">
          {quality.concerns.map((concern) => (
            <li key={concern.id}>
              <Notice
                tone={
                  concern.severity === "high"
                    ? "danger"
                    : concern.severity === "medium"
                      ? "warn"
                      : "neutral"
                }
              >
                <span className="block">{concern.message}</span>
                {concern.fix ? (
                  <span className="mt-0.5 block text-[11px] opacity-80">
                    {concern.fix}
                  </span>
                ) : null}
              </Notice>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

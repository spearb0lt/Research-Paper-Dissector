"use client";

/**
 * Upload with live progress, and the ingest options that matter.
 *
 * Options are shown rather than hidden behind an accordion because each one
 * changes the result materially and two of them cost real time. A control the
 * runtime cannot support renders disabled carrying the server's own reason, so
 * the difference between "not offered" and "not possible here" is always
 * visible.
 */

import { useCallback, useRef, useState } from "react";

import { uploadPaperStreaming } from "@/lib/api";
import type { ProgressEvent } from "@/lib/types";

import { useConfig } from "./ConfigProvider";
import { Card, ErrorNotice, Notice, ProgressBar, Select, Toggle, cx } from "./ui";

export function UploadPanel({
  collectionId,
  onDone,
  extra,
}: {
  collectionId?: number;
  onDone: (paperId: number) => void;
  /** Rendered between the drop zone and the options, for the link input. */
  extra?: React.ReactNode;
}) {
  const { config, can, why, prefs, setPrefs } = useConfig();
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const deepAvailable = can("deep_parse");
  const ocrAvailable = can("ocr");

  const parseMode = prefs.parseMode ?? config?.defaults.parse_mode ?? "auto";
  const dense = prefs.dense ?? config?.defaults.dense_index ?? true;
  const ocr = (prefs.ocr ?? config?.defaults.ocr ?? false) && ocrAvailable;

  const upload = useCallback(
    async (file: File) => {
      setBusy(true);
      setError(null);
      setWarnings([]);
      setProgress(0);
      setStage("Uploading");

      try {
        await uploadPaperStreaming(
          file,
          {
            collection_id: collectionId,
            parse_mode: parseMode,
            dense,
            ocr,
            embedding_provider: prefs.embedder,
          },
          (event: ProgressEvent) => {
            if (event.type === "progress") {
              setProgress(event.fraction);
              setStage(event.message);
            } else if (event.type === "done") {
              setWarnings(event.result.warnings ?? []);
              setProgress(1);
              setStage(
                event.result.reused
                  ? "Already in the library"
                  : `${event.result.element_count} elements, ${event.result.chunk_count} chunks`,
              );
              onDone(event.result.paper_id);
            } else {
              setError({ message: event.error.message, hint: event.error.hint });
            }
          },
        );
      } catch (exception) {
        setError(exception);
      } finally {
        setBusy(false);
      }
    },
    [collectionId, parseMode, dense, ocr, prefs.embedder, onDone],
  );

  const handleFiles = useCallback(
    (files: FileList | null) => {
      const file = files?.[0];
      if (!file) return;
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setError({ message: "That is not a PDF.", hint: "Only PDF files can be parsed." });
        return;
      }
      void upload(file);
    },
    [upload],
  );

  return (
    <Card className="p-4">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          handleFiles(event.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={cx(
          "cursor-pointer rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors",
          dragging ? "border-accent bg-accent-soft" : "border-line hover:border-line-strong",
          busy && "pointer-events-none opacity-60",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(event) => handleFiles(event.target.files)}
        />
        <p className="text-sm font-medium text-ink">
          {busy ? "Working..." : "Drop a PDF here, or click to choose one"}
        </p>
        <p className="mt-1 text-xs text-ink-faint">
          The same file uploaded twice reuses the existing parse.
        </p>
      </div>

      {busy || progress > 0 ? (
        <div className="mt-3">
          <ProgressBar value={progress} label={stage} />
        </div>
      ) : null}

      {error ? (
        <div className="mt-3">
          <ErrorNotice error={error} />
        </div>
      ) : null}

      {warnings.length ? (
        <div className="mt-3">
          <Notice tone="warn" title="Parsed with warnings">
            <ul className="list-disc space-y-0.5 pl-4">
              {warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Notice>
        </div>
      ) : null}

      {extra ? <div className="mt-3 border-t border-line pt-3">{extra}</div> : null}

      <div className="mt-4 space-y-3 border-t border-line pt-4">
        <Select
          label="Extraction"
          value={parseMode}
          onChange={(value) => setPrefs({ parseMode: value })}
          options={[
            { value: "auto", label: deepAvailable ? "Auto (deep)" : "Auto (fast)" },
            { value: "fast", label: "Fast: text, ruled tables, figures" },
            {
              value: "deep",
              label: deepAvailable
                ? "Deep: layout model and TableFormer"
                : "Deep (unavailable here)",
              disabled: !deepAvailable,
            },
          ]}
        />
        <Toggle
          checked={dense}
          onChange={(value) => setPrefs({ dense: value })}
          label="Build the vector index"
          hint="Runs the bundled local encoder. No key, no cost. Off leaves keyword search only, which is faster and still finds exact values."
        />
        <Toggle
          checked={ocr}
          disabled={!ocrAvailable}
          reason={why("ocr")}
          onChange={(value) => setPrefs({ ocr: value })}
          label="Read text inside figures"
          hint="OCR over every figure and any scanned page. Slow, and the only way to search a chart's axis labels."
        />
      </div>

      {!deepAvailable ? (
        <p className="mt-3 text-xs text-ink-faint">
          Deep extraction is unavailable: {why("deep_parse")}
        </p>
      ) : null}
    </Card>
  );
}

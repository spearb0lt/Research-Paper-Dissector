"use client";

/**
 * The question and answer surface, shared by the single paper tab and the
 * cross paper screen.
 *
 * Two things here are deliberate.
 *
 * **Evidence only is not a degraded mode.** It returns ranked chunks with
 * pages, scores and a per leg breakdown, which answers "where in this paper is
 * X" exactly, costs nothing and needs no key. It is the default when no model
 * is configured, and the toggle stays visible when one is.
 *
 * **Answers stream.** A lens on a long paper took 112 seconds to return, and
 * the screen showed a spinner for all of it. The same wait spent watching the
 * evidence arrive and then the answer being written reads as working rather
 * than broken, and nothing about the answer itself changed.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ask, askStreaming } from "@/lib/api";
import type {
  AnswerEvent,
  AnswerResult,
  Citation,
  LensGroup,
  SearchResult,
} from "@/lib/types";

import { EvidenceCard, Markdown, RetrievalSummary } from "./AnswerBody";
import { useConfig, useHasLlm } from "./ConfigProvider";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNotice,
  Notice,
  SectionTitle,
  Select,
  Spinner,
  TextInput,
  Toggle,
} from "./ui";

const SUGGESTIONS = [
  "What dataset did they use, and how big is it?",
  "What are the main limitations?",
  "What baselines did they compare against?",
  "Which numbers support the headline claim?",
];

export function AskPanel({
  paperIds,
  title,
  lensGroups,
}: {
  paperIds: number[];
  title?: string;
  /** Overrides the single paper lenses, for the cross paper screen. */
  lensGroups?: LensGroup[];
}) {
  const { config, can, why, prefs, setPrefs } = useConfig();
  const hasLlm = useHasLlm();

  const [question, setQuestion] = useState("");
  const [lens, setLens] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<unknown>(null);

  // Streamed state, kept apart from the final result so the partial text can be
  // shown while it arrives and replaced once citations have been validated.
  const [partial, setPartial] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [retrieval, setRetrieval] = useState<SearchResult | null>(null);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [history, setHistory] = useState<{ question: string; answer: AnswerResult }[]>([]);

  const abort = useRef<AbortController | null>(null);
  const answerRef = useRef<HTMLDivElement>(null);

  const useLlm = (prefs.useLlm ?? true) && hasLlm;
  const useRerank = (prefs.rerank ?? false) && can("rerank");

  useEffect(() => {
    if (!hasLlm && prefs.useLlm) setPrefs({ useLlm: false });
  }, [hasLlm, prefs.useLlm, setPrefs]);

  useEffect(() => () => abort.current?.abort(), []);

  const groups = lensGroups ?? config?.lenses ?? [];
  const allLenses = groups.flatMap((group) => group.lenses);

  const run = useCallback(
    async (overrideQuestion?: string, overrideLens?: string) => {
      const text = (overrideQuestion ?? question).trim();
      const chosenLens = overrideLens ?? lens;
      if (!text && !chosenLens) return;

      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;

      setBusy(true);
      setError(null);
      setPartial("");
      setResult(null);
      setCitations([]);
      setRetrieval(null);
      setStatus("Starting");

      const body = {
        paper_ids: paperIds,
        question: text,
        lens: chosenLens,
        use_dense: true,
        use_rerank: useRerank,
        provider: prefs.provider || undefined,
        model: prefs.model || undefined,
        embedding_provider: prefs.embedder || undefined,
        top_k: prefs.topK,
      };

      try {
        if (!useLlm) {
          // No model wanted, so there is no stream to open: one request returns
          // the ranked evidence and nothing else.
          const answer = await ask({ ...body, use_llm: false });
          setResult(answer);
          setCitations(answer.citations);
          setRetrieval(answer.retrieval);
          setHistory((current) => [...current, { question: text || chosenLens, answer }]);
          return;
        }

        await askStreaming(
          body,
          (event: AnswerEvent) => {
            if (event.type === "status") {
              setStatus(event.message);
            } else if (event.type === "evidence") {
              setCitations(event.citations);
              setRetrieval(event.retrieval);
            } else if (event.type === "delta") {
              setPartial((current) => current + event.text);
            } else if (event.type === "done") {
              // The discriminant is dropped: everything else on a done event
              // is exactly an AnswerResult.
              const answer = { ...event } as Partial<AnswerEvent> & AnswerResult;
              delete answer.type;
              setResult(answer as AnswerResult);
              setCitations(answer.citations);
              setRetrieval(answer.retrieval);
              setPartial("");
              setHistory((current) => [
                ...current,
                { question: text || chosenLens, answer: answer as AnswerResult },
              ]);
            }
          },
          controller.signal,
        );
      } catch (exception) {
        if (!controller.signal.aborted) setError(exception);
      } finally {
        if (!controller.signal.aborted) {
          setBusy(false);
          setStatus("");
        }
      }
    },
    [question, lens, paperIds, useLlm, useRerank, prefs],
  );

  const jumpToEvidence = useCallback((n: number) => {
    setHighlighted(n);
    document
      .getElementById(`evidence-${n}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  const hits = retrieval?.hits ?? [];
  const hitById = new Map(hits.map((hit) => [hit.chunk_id, hit]));
  const shownText = result?.text || partial;

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
      <div>
        <Card className="p-4">
          <TextInput
            value={question}
            onChange={setQuestion}
            onEnter={() => run()}
            label={title ?? "Ask this paper"}
            placeholder="What was the learning rate, and how was it scheduled?"
            autoFocus
          />

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              onClick={() => run()}
              disabled={busy || !question.trim()}
            >
              {busy ? "Working..." : useLlm ? "Answer with citations" : "Find the evidence"}
            </Button>
            {busy ? (
              <Button
                variant="ghost"
                onClick={() => {
                  abort.current?.abort();
                  setBusy(false);
                  setStatus("");
                }}
              >
                Stop
              </Button>
            ) : null}
            <Toggle
              checked={useLlm}
              disabled={!hasLlm}
              reason="No language model is configured. Retrieval still works fully."
              onChange={(value) => setPrefs({ useLlm: value })}
              label="Write an answer"
            />
            <Toggle
              checked={useRerank}
              disabled={!can("rerank")}
              reason={why("rerank")}
              onChange={(value) => setPrefs({ rerank: value })}
              label="Rerank"
            />
          </div>

          {!question && !shownText && !busy ? (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => {
                    setQuestion(suggestion);
                    void run(suggestion);
                  }}
                  className="rounded-lg border border-line px-2.5 py-1 text-xs text-ink-muted hover:bg-raised hover:text-ink"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          ) : null}
        </Card>

        {error ? (
          <div className="mt-4">
            <ErrorNotice error={error} />
          </div>
        ) : null}

        {busy && !shownText ? (
          <div className="py-8 text-center">
            <Spinner label={status || "Working"} />
            {citations.length ? (
              <p className="mt-2 text-xs text-ink-faint">
                {citations.length} excerpts found, listed on the right. The
                answer is being written from them now.
              </p>
            ) : null}
          </div>
        ) : null}

        <div ref={answerRef}>
          {result?.note ? (
            <div className="mt-4">
              <Notice tone={result.evidence_only && !hasLlm ? "neutral" : "warn"}>
                {result.note}
              </Notice>
            </div>
          ) : null}

          {shownText ? (
            <Card className="mt-4 p-4">
              <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-ink-faint">
                <Badge tone="accent">
                  {result ? result.model || result.provider : "writing"}
                </Badge>
                {result?.usage?.input_tokens ? (
                  <span>
                    {result.usage.input_tokens} in / {result.usage.output_tokens} out
                  </span>
                ) : null}
                {result?.lens ? <Badge>{result.lens}</Badge> : null}
                {result && result.images_sent > 0 ? (
                  <Badge
                    tone="accent"
                    title="Retrieved figures were sent to the model as images, so it read the chart rather than only its caption."
                  >
                    read {result.images_sent} figure
                    {result.images_sent === 1 ? "" : "s"}
                  </Badge>
                ) : null}
              </div>
              <Markdown text={shownText} onCite={jumpToEvidence} />
              {busy ? (
                <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-accent align-text-bottom" />
              ) : null}
              {result ? (
                <p className="mt-4 border-t border-line pt-2.5 text-[11px] text-ink-faint">
                  Every claim above is drawn only from the excerpts on the right.
                  A citation that pointed at an excerpt that was never supplied
                  has been removed, so an uncited sentence is unsupported.
                </p>
              ) : null}
            </Card>
          ) : null}

          {retrieval ? (
            <div className="mt-3">
              <RetrievalSummary retrieval={retrieval} />
            </div>
          ) : null}
        </div>

        {history.length > 1 ? (
          <div className="mt-6">
            <SectionTitle>Earlier in this session</SectionTitle>
            <ul className="space-y-1.5">
              {history
                .slice(0, -1)
                .reverse()
                .map((entry, index) => (
                  <li key={index}>
                    <button
                      onClick={() => {
                        setResult(entry.answer);
                        setCitations(entry.answer.citations);
                        setRetrieval(entry.answer.retrieval);
                        setPartial("");
                      }}
                      className="w-full truncate rounded-lg border border-line px-3 py-1.5 text-left text-xs text-ink-muted hover:bg-raised"
                    >
                      {entry.question}
                    </button>
                  </li>
                ))}
            </ul>
          </div>
        ) : null}
      </div>

      <aside className="space-y-4 lg:sticky lg:top-16 lg:max-h-[calc(100vh-5rem)] lg:self-start lg:overflow-y-auto">
        {allLenses.length ? (
          <Card className="p-3">
            <SectionTitle>
              {lensGroups ? "Compare them" : "Ready made analyses"}
            </SectionTitle>
            <Select
              value={lens}
              onChange={(value) => {
                setLens(value);
                if (value) void run("", value);
              }}
              options={[
                { value: "", label: "Pick one..." },
                ...groups.flatMap((group) =>
                  group.lenses.map((entry) => ({
                    value: entry.id,
                    label: lensGroups ? entry.label : `${group.label}: ${entry.label}`,
                  })),
                ),
              ]}
            />
            {lens ? (
              <p className="mt-1.5 text-[11px] text-ink-faint">
                {allLenses.find((entry) => entry.id === lens)?.description}
              </p>
            ) : null}
          </Card>
        ) : null}

        <div>
          <SectionTitle>
            {citations.length ? `Evidence (${citations.length})` : "Evidence"}
          </SectionTitle>
          {citations.length ? (
            <ul className="space-y-2">
              {citations.map((citation) => (
                <EvidenceCard
                  key={citation.n}
                  citation={citation}
                  hit={hitById.get(citation.chunk_id)}
                  highlighted={highlighted === citation.n}
                />
              ))}
            </ul>
          ) : (
            <Empty title="Nothing retrieved yet">
              Ask something, and the excerpts used will appear here with their
              pages and scores, before the answer is written.
            </Empty>
          )}
        </div>
      </aside>
    </div>
  );
}

/**
 * The one place the browser talks to the backend.
 *
 * Every request goes through `request()`, which is what makes the bring your
 * own key promise checkable: credentials are read from localStorage and
 * attached as headers here and nowhere else, they never appear in a URL, and
 * nothing in this file logs a request body or a header.
 */

import { credentialHeaders } from "./keys";
import type {
  Analysis,
  AnswerEvent,
  AnswerResult,
  BatchEvent,
  AppConfig,
  Chunk,
  Collection,
  Element,
  FindResult,
  Identified,
  IngestResult,
  LensGroup,
  Message,
  Note,
  NoteColour,
  Paper,
  ProgressEvent,
  Quality,
  Reference,
  SearchResult,
  Thread,
} from "./types";

/**
 * An error carrying the backend's own wording. `hint` is written to tell the
 * reader what to do next, so every error surface in the UI renders it.
 */
export class ApiError extends Error {
  readonly hint: string;
  readonly status: number;
  readonly provider: string;

  constructor(message: string, options: { hint?: string; status?: number; provider?: string } = {}) {
    super(message);
    this.name = "ApiError";
    this.hint = options.hint ?? "";
    this.status = options.status ?? 0;
    this.provider = options.provider ?? "";
  }
}

/**
 * Where the API lives.
 *
 * Empty in production, so the browser calls the same origin and Vercel routes
 * /api/* to the Python function. In development it is set to the backend's own
 * origin, because Next's dev server rewrite buffers a streamed response: every
 * server sent event arrives at once when the request finishes. Measured on the
 * answer stream, the first event arrived at 0.0s direct and at 19.0s through
 * the rewrite, which makes streaming look broken exactly where it is being
 * developed. CORS is already open on the backend, and credentials travel as
 * headers, so talking to it directly changes nothing else.
 */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export function apiUrl(path: string): string {
  return `${API_BASE}/api${path.startsWith("/") ? path : `/${path}`}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toApiError(payload: unknown, status: number): ApiError {
  if (isRecord(payload)) {
    const message =
      typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : typeof payload.detail === "string"
          ? payload.detail
          : `Request failed with status ${status}.`;
    return new ApiError(message, {
      hint: typeof payload.hint === "string" ? payload.hint : "",
      provider: typeof payload.provider === "string" ? payload.provider : "",
      status,
    });
  }
  return new ApiError(`Request failed with status ${status}.`, { status });
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  for (const [name, value] of Object.entries(credentialHeaders())) headers.set(name, value);
  // FormData sets its own multipart boundary, so a Content-Type here would
  // corrupt the body.
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(path), { ...init, headers });
  } catch {
    throw new ApiError("Could not reach the server.", {
      hint: "Check that the backend is running and that you are online.",
    });
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      if (!response.ok) throw new ApiError(text.slice(0, 300), { status: response.status });
    }
  }
  if (!response.ok) throw toApiError(payload, response.status);
  return payload as T;
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

// ------------------------------------------------------------------ config

export const getConfig = () => request<AppConfig>("/config");

export const getHealth = () =>
  request<{ ok: boolean; app: string; checks: Record<string, unknown> }>("/health");

export const verifyProvider = (provider: string) =>
  request<{ ok: boolean; models?: string[]; message?: string; hint?: string }>(
    `/providers/verify${query({ provider })}`,
    { method: "POST" },
  );

// ------------------------------------------------------------- collections

export const listCollections = () =>
  request<{ collections: Collection[] }>("/collections").then((r) => r.collections);

export const createCollection = (name: string, description = "") =>
  request<{ collection: Collection }>("/collections", {
    method: "POST",
    body: JSON.stringify({ name, description }),
  }).then((r) => r.collection);

export const deleteCollection = (id: number) =>
  request<{ ok: boolean }>(`/collections/${id}`, { method: "DELETE" });

// ------------------------------------------------------------------ papers

export const listPapers = (options: {
  collection_id?: number;
  q?: string;
  status?: string;
} = {}) => request<{ papers: Paper[] }>(`/papers${query(options)}`).then((r) => r.papers);

export const getPaper = (id: number) =>
  request<{
    paper: Paper;
    element_counts: Record<string, number>;
    section_counts: Record<string, number>;
  }>(`/papers/${id}`);

export const deletePaper = (id: number) =>
  request<{ ok: boolean }>(`/papers/${id}`, { method: "DELETE" });

export interface UploadOptions {
  collection_id?: number;
  parse_mode?: string;
  dense?: boolean;
  ocr?: boolean;
  embedding_provider?: string;
  force?: boolean;
}

function uploadForm(file: File, options: UploadOptions): FormData {
  const form = new FormData();
  form.set("file", file);
  for (const [key, value] of Object.entries(options)) {
    if (value === undefined || value === "") continue;
    form.set(key, String(value));
  }
  return form;
}

export const uploadPaper = (file: File, options: UploadOptions = {}) =>
  request<{ result: IngestResult; paper: Paper }>("/papers", {
    method: "POST",
    body: uploadForm(file, options),
  });

/**
 * Upload with progress, reading the server sent event stream.
 *
 * Written by hand rather than with EventSource because EventSource cannot send
 * a POST body, and the file has to go up in the same request that reports on
 * it. The buffer split on a blank line is the SSE framing.
 */
export async function uploadPaperStreaming(
  file: File,
  options: UploadOptions,
  onEvent: (event: ProgressEvent) => void,
): Promise<void> {
  const headers = new Headers();
  for (const [name, value] of Object.entries(credentialHeaders())) headers.set(name, value);

  const response = await fetch(apiUrl("/papers/stream"), {
    method: "POST",
    body: uploadForm(file, options),
    headers,
  });

  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "");
    let payload: unknown = null;
    try {
      payload = JSON.parse(text);
    } catch {
      /* fall through to the generic error below */
    }
    throw toApiError(payload, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(6)) as ProgressEvent);
        } catch {
          // A frame that will not parse is dropped rather than aborting an
          // upload that is otherwise proceeding.
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export const reindexPaper = (
  id: number,
  body: {
    dense?: boolean;
    embedding_provider?: string;
    chunk_tokens?: number;
    chunk_overlap?: number;
  },
) =>
  request<{ chunk_count: number; dense_enabled: boolean; paper: Paper }>(
    `/papers/${id}/reindex`,
    { method: "POST", body: JSON.stringify(body) },
  );

// ---------------------------------------------------------------- elements

export const listElements = (
  paperId: number,
  options: { kind?: string; section?: string; page?: number; limit?: number } = {},
) =>
  request<{
    elements: Element[];
    counts: Record<string, number>;
    sections: Record<string, number>;
  }>(`/papers/${paperId}/elements${query(options)}`);

export const getOutline = (paperId: number) =>
  request<{ headings: Element[]; section_counts: Record<string, number> }>(
    `/papers/${paperId}/outline`,
  );

export const listChunks = (paperId: number) =>
  request<{ chunks: Chunk[] }>(`/papers/${paperId}/chunks`).then((r) => r.chunks);

/** A content addressed URL, so the browser can cache it permanently. */
export const blobUrl = (digest: string, kind: "image" | "pdf" = "image") =>
  digest ? apiUrl(`/blob/${digest}${kind === "pdf" ? "?kind=pdf" : ""}`) : "";

// ------------------------------------------------------------------ search

export const search = (
  paperIds: number[],
  q: string,
  options: {
    top_k?: number;
    dense?: boolean;
    lexical?: boolean;
    rerank_hits?: boolean;
    kind?: string;
    section?: string;
  } = {},
) =>
  request<SearchResult>(
    `/search${query({ paper_ids: paperIds.join(","), q, ...options })}`,
  );

export const findEverywhere = (paperIds: number[], q: string, limit = 200) =>
  request<FindResult>(`/find${query({ paper_ids: paperIds.join(","), q, limit })}`);

// ------------------------------------------------------------------ asking

export interface AskBody {
  paper_ids: number[];
  question?: string;
  lens?: string;
  thread_id?: number;
  use_llm?: boolean;
  use_dense?: boolean;
  use_rerank?: boolean;
  top_k?: number;
  provider?: string;
  model?: string;
  embedding_provider?: string;
}

export const ask = (body: AskBody) =>
  request<AnswerResult>("/ask", { method: "POST", body: JSON.stringify(body) });

export const runLens = (
  paperId: number,
  lensId: string,
  body: { provider?: string; model?: string; refresh?: boolean } = {},
) =>
  request<{ analysis: Analysis | null; cached: boolean; note?: string }>(
    `/papers/${paperId}/lenses/${lensId}`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const listPaperLenses = (paperId: number) =>
  request<{ analyses: { lens: string; created_at: string }[] }>(`/papers/${paperId}/lenses`);

// ----------------------------------------------------------------- threads

export const createThread = (body: {
  collection_id?: number;
  paper_ids: number[];
  title?: string;
}) => request<{ thread: Thread }>("/threads", { method: "POST", body: JSON.stringify(body) })
  .then((r) => r.thread);

export const listThreads = (options: { collection_id?: number; paper_id?: number } = {}) =>
  request<{ threads: Thread[] }>(`/threads${query(options)}`).then((r) => r.threads);

export const getThread = (id: number) =>
  request<{ thread: Thread; messages: Message[] }>(`/threads/${id}`);

export const deleteThread = (id: number) =>
  request<{ ok: boolean }>(`/threads/${id}`, { method: "DELETE" });

// ------------------------------------------------------------------- usage

export const getUsage = (paperId?: number) =>
  request<{
    totals: Record<string, number>;
    by_model: Record<string, unknown>[];
  }>(`/usage${query({ paper_id: paperId })}`);

// ------------------------------------------------- fetching by identifier

export const identifySource = (source: string) =>
  request<{ identified: Identified }>("/papers/fetch/identify", {
    method: "POST",
    body: JSON.stringify({ source }),
  }).then((r) => r.identified);

export const fetchPaper = (
  source: string,
  options: { collection_id?: number; parse_mode?: string; dense?: boolean; ocr?: boolean } = {},
) =>
  request<{ result: IngestResult; paper: Paper }>("/papers/fetch", {
    method: "POST",
    body: JSON.stringify({ source, ...options }),
  });

// ----------------------------------------------------------- references

export const listReferences = (paperId: number, resolve = false) =>
  request<{ references: Reference[]; resolved: boolean }>(
    `/papers/${paperId}/references${query({ resolve, limit: 400 })}`,
  ).then((r) => r.references);

export const addReference = (
  paperId: number,
  elementId: number,
  options: { collection_id?: number; parse_mode?: string; dense?: boolean } = {},
) =>
  request<{ result: IngestResult; paper: Paper }>(
    `/papers/${paperId}/references/${elementId}/add`,
    { method: "POST", body: JSON.stringify(options) },
  );

// --------------------------------------------------------------- triage

export const setTriage = (
  paperId: number,
  body: { read_state?: string; verdict?: string },
) =>
  request<{ paper: Paper }>(`/papers/${paperId}/triage`, {
    method: "PATCH",
    body: JSON.stringify(body),
  }).then((r) => r.paper);

export const getQuality = (paperId: number) =>
  request<{ quality: Quality }>(`/papers/${paperId}/quality`).then((r) => r.quality);

// -------------------------------------------------------- page rendering

/** The rendered image of one page. Rendered on first request, cached after. */
export const pageImageUrl = (paperId: number, page: number) =>
  apiUrl(`/papers/${paperId}/page/${page}`);

/** The original PDF, for embedding or opening. */
export const pdfUrl = (digest: string) => blobUrl(digest, "pdf");

// --------------------------------------------------------------- exports

export const exportMarkdownUrl = (paperId: number) => apiUrl(`/papers/${paperId}/export.md`);
export const exportBibtexUrl = (paperId: number) => apiUrl(`/papers/${paperId}/export.bib`);
export const exportTableUrl = (paperId: number, elementId: number) =>
  apiUrl(`/papers/${paperId}/elements/${elementId}/csv`);
export const exportFigureUrl = (paperId: number, elementId: number) =>
  apiUrl(`/papers/${paperId}/figure/${elementId}`);
export const exportLibraryUrl = (collectionId?: number) =>
  apiUrl(`/library/export.md${collectionId ? `?collection_id=${collectionId}` : ""}`);

// ------------------------------------------------------- comparison lenses

export const listComparisonLenses = () =>
  request<{ groups: LensGroup[] }>("/lenses/comparison").then((r) => r.groups);

// ------------------------------------------------------- streamed answers

/** `use_llm` has no meaning here: streaming exists to show a model writing. */
export type AskStreamBody = Omit<AskBody, "use_llm">;

/**
 * Ask and receive the answer as it is written.
 *
 * Written by hand rather than with EventSource for the same reason as the
 * upload stream: EventSource cannot send a POST body, and the question and the
 * paper selection have to go up in the request that answers them.
 */
export async function askStreaming(
  body: AskStreamBody,
  onEvent: (event: AnswerEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers({ "Content-Type": "application/json" });
  for (const [name, value] of Object.entries(credentialHeaders())) headers.set(name, value);

  const response = await fetch(apiUrl("/ask/stream"), {
    method: "POST",
    body: JSON.stringify(body),
    headers,
    signal,
  });

  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "");
    let payload: unknown = null;
    try {
      payload = JSON.parse(text);
    } catch {
      /* fall through */
    }
    throw toApiError(payload, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(6)) as AnswerEvent);
        } catch {
          // A frame that will not parse is dropped rather than aborting an
          // answer that is otherwise arriving.
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

// ----------------------------------------------------------------- notes

export const listNotes = (paperId: number) =>
  request<{ notes: Note[]; counts: Record<string, number> }>(`/papers/${paperId}/notes`);

export const createNote = (
  paperId: number,
  body: { body: string; element_id?: number | null; colour?: NoteColour },
) =>
  request<{ note: Note }>(`/papers/${paperId}/notes`, {
    method: "POST",
    body: JSON.stringify(body),
  }).then((r) => r.note);

export const updateNote = (
  noteId: number,
  body: { body?: string; colour?: NoteColour },
) =>
  request<{ note: Note }>(`/notes/${noteId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  }).then((r) => r.note);

export const deleteNote = (noteId: number) =>
  request<{ ok: boolean }>(`/notes/${noteId}`, { method: "DELETE" });

// ------------------------------------------------------------ batch lens

/**
 * Run one lens over many papers, receiving each result as it lands.
 *
 * Sequential on the server, because every free tier rate limits per minute and
 * firing ten requests at once is the reliable way to get nine rejected.
 */
export async function runBatchLens(
  body: { paper_ids: number[]; lens: string; provider?: string; model?: string; refresh?: boolean },
  onEvent: (event: BatchEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers({ "Content-Type": "application/json" });
  for (const [name, value] of Object.entries(credentialHeaders())) headers.set(name, value);

  const response = await fetch(apiUrl("/lenses/batch"), {
    method: "POST",
    body: JSON.stringify(body),
    headers,
    signal,
  });
  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "");
    let payload: unknown = null;
    try {
      payload = JSON.parse(text);
    } catch {
      /* fall through */
    }
    throw toApiError(payload, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(6)) as BatchEvent);
        } catch {
          // A frame that will not parse is dropped rather than aborting a run
          // whose other results are arriving fine.
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

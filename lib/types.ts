// Shapes the API returns. Kept in one file so a change to a route's response
// breaks the type check rather than a screen at runtime.

export type Capability = {
  id: string;
  label: string;
  available: boolean;
  reason: string;
};

export type RuntimeInfo = {
  tier: "server" | "serverless";
  platform: string;
  python_version: string;
  max_job_seconds: number;
  writable_dir: string;
  persistent_disk: boolean;
  capabilities: Capability[];
};

export type ParserStatus = {
  id: string;
  label: string;
  available: boolean;
  reason: string;
  quality: number;
};

export type ModelSpec = { id: string; label: string; note: string; cheap: boolean };

export type ProviderStatus = {
  id: string;
  label: string;
  available: boolean;
  reason: string;
  models: ModelSpec[];
  default_model: string;
  docs_url: string;
  key_names: string[];
  key_source: "" | "client" | "server";
  needs_account: boolean;
  has_account: boolean;
  local: boolean;
};

export type EmbedderStatus = {
  id: string;
  label: string;
  available: boolean;
  reason: string;
  local: boolean;
  quality: number;
  model: string;
  dim: number;
  docs_url?: string;
};

export type LensSpec = {
  id: string;
  label: string;
  description: string;
  group: string;
  wants_images: boolean;
};

export type LensGroup = { id: string; label: string; lenses: LensSpec[] };

export type AppConfig = {
  app_name: string;
  tagline: string;
  allow_client_keys: boolean;
  runtime: RuntimeInfo;
  parsers: ParserStatus[];
  providers: ProviderStatus[];
  embedders: EmbedderStatus[];
  lenses: LensGroup[];
  rerank: { available: boolean; reason: string; model: string };
  defaults: {
    parse_mode: string;
    chunk_tokens: number;
    chunk_overlap: number;
    dense_index: boolean;
    ocr: boolean;
    top_k: number;
    embedding_provider: string;
  };
  storage: { persistent: boolean; reason: string };
};

export type Collection = {
  id: number;
  name: string;
  slug: string;
  description: string;
  paper_count?: number;
  created_at: string;
  updated_at: string;
};

export type PageInfo = {
  number: number;
  width: number;
  height: number;
  render_digest: string;
  needs_ocr: boolean;
};

export type Paper = {
  id: number;
  collection_id: number | null;
  file_hash: string;
  filename: string;
  blob_digest: string;
  title: string;
  title_source: string;
  authors: string[];
  abstract: string;
  doi: string;
  arxiv_id: string;
  year: number;
  venue: string;
  keywords: string[];
  num_pages: number;
  toc: { level: number; title: string; page: number }[];
  pages: PageInfo[];
  status: "pending" | "parsing" | "indexing" | "ready" | "failed";
  status_detail: string;
  parser: string;
  parser_version: string;
  parse_seconds: number;
  warnings: string[];
  counts: Record<string, number>;
  dense_signature: string;
  chunk_tokens: number;
  chunk_overlap: number;
  indexed_at: string | null;
  read_state: ReadState;
  verdict: string;
  source_url: string;
  quality: Quality | null;
  created_at: string;
  updated_at: string;
};

export type ReadState = "unread" | "reading" | "read" | "rejected";

export type QualityConcern = {
  id: string;
  severity: "high" | "medium" | "low";
  message: string;
  fix: string;
};

export type Quality = {
  score: number;
  grade: "good" | "fair" | "poor";
  concerns: QualityConcern[];
  stats: Record<string, number | string>;
};

export type ResolvedRef = {
  title: string;
  authors: string[];
  year: number;
  venue: string;
  doi: string;
  arxiv_id: string;
  view_url: string;
  pdf_url: string;
  source: "printed" | "arxiv" | "crossref" | "";
  confidence: number;
};

export type Reference = {
  element_id: number;
  number: number | null;
  text: string;
  page: number;
  title: string;
  authors: string[];
  year: number | null;
  resolved: ResolvedRef | null;
  can_add: boolean;
  in_library: number | null;
};

export type Identified = {
  kind: "arxiv" | "doi" | "url";
  arxiv_id?: string;
  doi?: string;
  pdf_url: string;
  view_url: string;
};

export type NoteColour = "yellow" | "green" | "blue" | "pink" | "grey";

export type Note = {
  id: number;
  paper_id: number;
  element_id: number | null;
  body: string;
  colour: NoteColour;
  created_at: string;
  updated_at: string;
  /** Joined from the element this note points at, when it points at one. */
  element_page?: number | null;
  element_kind?: string | null;
  element_label?: string | null;
  element_text?: string | null;
  paper_title?: string | null;
};

/** One frame of a batch lens run. */
export type BatchEvent =
  | { type: "progress"; index: number; total: number; paper_id: number; title: string }
  | { type: "result"; paper_id: number; title: string; cached: boolean; analysis: Analysis }
  | { type: "failed"; paper_id: number; title: string; error: string }
  | { type: "done"; total: number };

/** One frame of a streamed answer. */
export type AnswerEvent =
  | { type: "status"; message: string }
  | { type: "evidence"; citations: Citation[]; retrieval: SearchResult }
  | { type: "delta"; text: string }
  | ({ type: "done" } & AnswerResult);

export type BBox = { x0: number; y0: number; x1: number; y1: number };

export type TableData = {
  grid: string[][];
  html: string;
  markdown: string;
  num_rows: number;
  num_cols: number;
  has_header?: boolean;
};

export type ImageData = {
  digest: string;
  width: number;
  height: number;
  ocr_text: string;
  description: string;
};

export type ElementKind =
  | "title" | "authors" | "abstract" | "heading" | "paragraph" | "list_item"
  | "table" | "figure" | "caption" | "formula" | "code" | "footnote"
  | "reference" | "form_field" | "page_header" | "page_footer" | "other";

export type Element = {
  id: number;
  paper_id: number;
  kind: ElementKind;
  text: string;
  search_text: string;
  page: number;
  bbox: BBox | null;
  ord: number;
  section: string;
  section_path: string[];
  level: number;
  label: string;
  caption: string;
  table: TableData | null;
  image: ImageData | null;
  linked_id: string;
  extra: Record<string, unknown>;
};

export type Chunk = {
  id: number;
  paper_id: number;
  ord: number;
  kind: string;
  text: string;
  display_text: string;
  page: number;
  section: string;
  section_path: string[];
  label: string;
  caption: string;
  element_ids: number[];
  token_estimate: number;
  extra: Record<string, unknown>;
};

export type LegInfo = { rank: number; score: number };

export type Hit = {
  chunk_id: number;
  paper_id: number;
  paper_title: string;
  kind: string;
  text: string;
  page: number;
  section: string;
  section_path: string[];
  label: string;
  caption: string;
  element_ids: number[];
  score: number;
  legs: Record<string, LegInfo>;
  rerank_score: number | null;
  image_digest: string;
  extra: Record<string, unknown>;
};

export type SearchResult = {
  hits: Hit[];
  legs_used: string[];
  legs_skipped: Record<string, string>;
  weights: Record<string, number>;
  rerank_used: boolean;
  candidate_count: number;
};

export type Citation = {
  n: number;
  chunk_id: number;
  paper_id: number;
  paper_title: string;
  kind: string;
  page: number;
  section: string;
  label: string;
  element_ids: number[];
  image_digest: string;
  score: number;
};

export type AnswerResult = {
  text: string;
  citations: Citation[];
  retrieval: SearchResult | null;
  provider: string;
  model: string;
  usage: { input_tokens?: number; output_tokens?: number; cost_usd?: number };
  lens: string;
  evidence_only: boolean;
  note: string;
  /** Figures sent to the model as images rather than as caption text. */
  images_sent: number;
};

export type FindSnippet = {
  text: string;
  prefix: string;
  suffix: string;
  spans: [number, number][];
};

export type FindMatch = {
  element_id: number;
  paper_id: number;
  paper_title: string;
  kind: ElementKind;
  page: number;
  section: string;
  label: string;
  caption: string;
  bbox: BBox | null;
  image_digest: string;
  snippets: FindSnippet[];
  where: string[];
};

export type FindResult = {
  total: number;
  by_kind: Record<string, number>;
  matches: FindMatch[];
};

export type IngestResult = {
  paper_id: number;
  created: boolean;
  reused: boolean;
  parse_seconds: number;
  index_seconds: number;
  element_count: number;
  chunk_count: number;
  dense_enabled: boolean;
  warnings: string[];
};

export type ProgressEvent =
  | { type: "progress"; stage: string; message: string; fraction: number }
  | { type: "done"; result: IngestResult; paper: Paper }
  | { type: "error"; error: { message: string; hint?: string } };

export type Thread = {
  id: number;
  collection_id: number | null;
  paper_id: number | null;
  title: string;
  paper_ids: number[];
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: number;
  thread_id: number;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  lens: string;
  provider: string;
  model: string;
  usage: Record<string, number>;
  retrieval: Record<string, unknown>;
  created_at: string;
};

export type Analysis = {
  id: number;
  paper_id: number;
  lens: string;
  content: string;
  citations: Citation[];
  provider: string;
  model: string;
  usage: Record<string, number>;
  created_at: string;
};

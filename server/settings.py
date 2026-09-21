"""Central configuration, read entirely from the environment.

Nothing here raises at import time. A missing credential means the matching
provider reports itself unavailable, so the app boots and does useful work with
whatever subset of keys happens to be present, including none at all. A paper
can be uploaded, parsed, indexed, searched and browsed with zero keys set.
"""
from __future__ import annotations

import os
from pathlib import Path

from .runtime import current as runtime

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = Path(os.environ.get("MODELS_DIR", "").strip() or (BASE_DIR / "Semantic Models"))

if os.environ.get("IGNORE_DOTENV", "").strip().lower() not in {"1", "true", "yes"}:
    try:
        from dotenv import load_dotenv

        _env_file = BASE_DIR / ".env"
        if _env_file.exists():
            load_dotenv(_env_file, override=False)
    except ImportError:  # python-dotenv is optional in a slim deployment
        pass


def env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw and raw.strip() else default
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw and raw.strip() else default
    except ValueError:
        return default


def env_list(name: str, default: str = "") -> list[str]:
    raw = env(name, default=default) or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


# ---------------------------------------------------------------- application

APP_NAME = env("APP_NAME", default="Dissect")
APP_TAGLINE = env(
    "APP_TAGLINE",
    default="Every figure, table and claim in a paper, searchable and citable",
)
ENVIRONMENT = env("ENVIRONMENT", default="production")
DEBUG = env_bool("DEBUG", False)
PUBLIC_BASE_URL = (env("PUBLIC_BASE_URL", "VERCEL_URL", default="") or "").rstrip("/")
if PUBLIC_BASE_URL and not PUBLIC_BASE_URL.startswith("http"):
    PUBLIC_BASE_URL = f"https://{PUBLIC_BASE_URL}"

CORS_ORIGINS = env_list("CORS_ORIGINS")
APP_PASSWORD = env("APP_PASSWORD")

# Bring your own key. When on, a visitor pastes their own provider key in the
# UI and it rides their requests only, so a public deployment costs the
# operator nothing. Turn off for a private instance that must use server keys.
ALLOW_CLIENT_KEYS = env_bool("ALLOW_CLIENT_KEYS", True)

# ------------------------------------------------------------------- storage

DATABASE_URL = env("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL", "NEON_DATABASE_URL")
DATA_DIR = Path(env("DATA_DIR", default=str(runtime().writable_dir)))
SQLITE_PATH = Path(env("SQLITE_PATH", default=str(DATA_DIR / "dissect.db")))
# Uploaded PDFs and extracted figure images. Kept out of the database because a
# 40 MB paper with 60 figures makes a mess of every query that touches its row.
BLOB_DIR = Path(env("BLOB_DIR", default=str(DATA_DIR / "blobs")))
MAX_UPLOAD_BYTES = env_int("MAX_UPLOAD_BYTES", 80_000_000)

# ---------------------------------------------------------- LLM provider keys

GOOGLE_API_KEY = env("GOOGLE_API_KEY", "GEMINI_API_KEY")
GROQ_API_KEY = env("GROQ_API_KEY")
OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_BASE_URL = env("OPENAI_BASE_URL", default="https://api.openai.com/v1")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")
MISTRAL_API_KEY = env("MISTRAL_API_KEY")
DEEPSEEK_API_KEY = env("DEEPSEEK_API_KEY")
TOGETHER_API_KEY = env("TOGETHER_API_KEY", "TOGETHERAI_API_KEY")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY")
CEREBRAS_API_KEY = env("CEREBRAS_API_KEY")
SAMBANOVA_API_KEY = env("SAMBANOVA_API_KEY")
XAI_API_KEY = env("XAI_API_KEY", "GROK_API_KEY")
FIREWORKS_API_KEY = env("FIREWORKS_API_KEY")
PERPLEXITY_API_KEY = env("PERPLEXITY_API_KEY")
HUGGINGFACE_API_KEY = env(
    "HUGGINGFACE_API_KEY", "HUGGINGFACE_API_TOKEN", "HF_TOKEN", "HF_API_KEY"
)
HUGGINGFACE_BASE_URL = env(
    "HUGGINGFACE_BASE_URL", "HF_BASE_URL", default="https://router.huggingface.co/v1"
)

# Cloudflare Workers AI needs two values, because the account id sits in the
# URL path rather than a header and a token alone addresses nothing.
CLOUDFLARE_API_TOKEN = env("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY", "CF_API_TOKEN")
CLOUDFLARE_ACCOUNT_ID = env("CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID")

# OpenAI protocol servers running on the operator's own machine. Only reachable
# from a server tier deployment, never from a serverless function.
OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", "OLLAMA_HOST", default="http://localhost:11434")
LMSTUDIO_BASE_URL = env("LMSTUDIO_BASE_URL", default="http://localhost:1234/v1")
LLAMACPP_BASE_URL = env("LLAMACPP_BASE_URL", default="http://localhost:8080/v1")
VLLM_BASE_URL = env("VLLM_BASE_URL", default="http://localhost:8000/v1")
VLLM_API_KEY = env("VLLM_API_KEY", default="EMPTY")

MODEL_OVERRIDES = {
    "gemini": env("GEMINI_MODEL"),
    "groq": env("GROQ_MODEL"),
    "openai": env("OPENAI_MODEL"),
    "anthropic": env("ANTHROPIC_MODEL"),
    "mistral": env("MISTRAL_MODEL"),
    "deepseek": env("DEEPSEEK_MODEL"),
    "together": env("TOGETHER_MODEL"),
    "openrouter": env("OPENROUTER_MODEL"),
    "cerebras": env("CEREBRAS_MODEL"),
    "sambanova": env("SAMBANOVA_MODEL"),
    "xai": env("XAI_MODEL"),
    "fireworks": env("FIREWORKS_MODEL"),
    "perplexity": env("PERPLEXITY_MODEL"),
    "huggingface": env("HUGGINGFACE_MODEL", "HF_MODEL"),
    "cloudflare": env("CLOUDFLARE_MODEL", "CF_MODEL"),
    "ollama": env("OLLAMA_MODEL"),
    "lmstudio": env("LMSTUDIO_MODEL"),
    "llamacpp": env("LLAMACPP_MODEL"),
    "vllm": env("VLLM_MODEL"),
}

EXTRA_MODELS = {
    key: env(f"{key.upper()}_EXTRA_MODELS", default="") for key in MODEL_OVERRIDES
}

# --------------------------------------------------------------- embeddings

# "auto" walks the registry in order and takes the first usable backend, which
# on a bare install is the bundled ONNX model and needs no key. A hosted
# embedding provider is never selected automatically even when its key is
# present: it has to be named here or chosen in the UI, because embedding a
# whole paper is the one operation that can quietly cost money.
EMBEDDING_PROVIDER = env("EMBEDDING_PROVIDER", default="auto")
EMBEDDING_MODEL = env("EMBEDDING_MODEL")
EMBEDDING_DIMENSIONS = env_int("EMBEDDING_DIMENSIONS", 0)  # 0 means provider default
EMBEDDING_BATCH_SIZE = env_int("EMBEDDING_BATCH_SIZE", 64)

LOCAL_EMBED_MODEL_DIR = Path(
    env("LOCAL_EMBED_MODEL_DIR", default=str(MODELS_DIR / "all-MiniLM-L6-v2-onnx"))
)
LOCAL_EMBED_ENABLED = env_bool("LOCAL_EMBED_ENABLED", True)

JINA_API_KEY = env("JINA_API_KEY")
VOYAGE_API_KEY = env("VOYAGE_API_KEY")
COHERE_API_KEY = env("COHERE_API_KEY")

# Two thresholds the ported embedding backends expect. They are not used for
# retrieval here, only for the near duplicate check that stops the same chunk
# being indexed twice when a paper is re-parsed with different settings.
DUPLICATE_THRESHOLD = env_float("DUPLICATE_THRESHOLD", 0.94)
CLUSTER_THRESHOLD = env_float("CLUSTER_THRESHOLD", 0.72)

# ---------------------------------------------------------------- parsing

# "fast" is PyMuPDF plus pdfplumber and runs anywhere in a few seconds.
# "deep" adds Docling's layout and table structure models and needs a real
# machine. "auto" picks deep when the runtime allows it and fast otherwise.
PARSE_MODE = env("PARSE_MODE", default="auto")
PARSE_TIMEOUT = env_int("PARSE_TIMEOUT", 900)
# Render figures at this multiple of the PDF's own resolution. Two is enough to
# read axis labels in a typical single column chart without quadrupling storage.
FIGURE_SCALE = env_float("FIGURE_SCALE", 2.0)
# Page images for the reader view and for any vision model call.
PAGE_RENDER_SCALE = env_float("PAGE_RENDER_SCALE", 1.6)
OCR_ENABLED = env_bool("OCR_ENABLED", False)
OCR_ENGINE = env("OCR_ENGINE", default="auto")  # auto, rapidocr, easyocr, tesseract
OCR_LANGUAGES = env_list("OCR_LANGUAGES", default="en")
# Run Docling's formula and code enrichment models. Off by default because each
# one is a separate model download and most papers do not need them.
FORMULA_ENRICHMENT = env_bool("FORMULA_ENRICHMENT", False)

# ---------------------------------------------------------------- indexing

# Token budget per chunk and the overlap between neighbours. Measured in
# tokens, not characters, so the number means the same thing across encoders.
CHUNK_TOKENS = env_int("CHUNK_TOKENS", 450)
CHUNK_OVERLAP = env_int("CHUNK_OVERLAP", 80)
# A chunk shorter than this is merged into its neighbour rather than indexed.
# Stray page numbers and running heads otherwise fill the results with noise.
MIN_CHUNK_CHARS = env_int("MIN_CHUNK_CHARS", 80)

# Dense embedding of chunks. On by default and local by default: the bundled
# ONNX encoder costs nothing and needs no key. Turning this off leaves BM25 as
# the only retrieval leg, which is fast and still answers exact value questions.
DENSE_INDEX_ENABLED = env_bool("DENSE_INDEX_ENABLED", True)
# Visual embedding of figures with CLIP. Reserved, and deliberately not
# implemented: a figure is already reachable by its caption, its label and the
# OCR of the text inside it, and is then read directly by a vision model, which
# between them cover what a CLIP text-to-image search was for. The `clip`
# runtime capability and these settings stay so the leg can be added without a
# migration, and `fuse.py` already weights a "visual" leg that nothing
# currently fills.
CLIP_INDEX_ENABLED = env_bool("CLIP_INDEX_ENABLED", False)
CLIP_MODEL = env("CLIP_MODEL", default="ViT-B-32")
CLIP_PRETRAINED = env("CLIP_PRETRAINED", default="laion2b_s34b_b79k")

# The LLM pass that writes a summary per element. Off by default: it is the one
# indexing step that costs money, and the deterministic section context added
# to every chunk recovers most of what it buys.
LLM_ENRICH_ENABLED = env_bool("LLM_ENRICH_ENABLED", False)

# --------------------------------------------------------------- retrieval

RETRIEVE_TOP_K = env_int("RETRIEVE_TOP_K", 12)
# Candidates each leg contributes before fusion. Wider than the final k so
# that reciprocal rank fusion has something to fuse.
CANDIDATE_K = env_int("CANDIDATE_K", 40)
# Reciprocal rank fusion's damping constant. 60 is the value from the original
# paper and the one every implementation that reports numbers uses.
RRF_K = env_int("RRF_K", 60)
RERANK_ENABLED = env_bool("RERANK_ENABLED", False)
RERANK_MODEL = env("RERANK_MODEL", default="cross-encoder/ms-marco-MiniLM-L-6-v2")
RERANK_TOP_K = env_int("RERANK_TOP_K", 8)

# ---------------------------------------------------------------- answering

ANSWER_TEMPERATURE = env_float("ANSWER_TEMPERATURE", 0.1)
ANSWER_MAX_TOKENS = env_int("ANSWER_MAX_TOKENS", 4096)
# Turns of conversation kept in the prompt. Beyond a handful the history
# crowds out the retrieved evidence, which is the wrong trade.
MEMORY_TURNS = env_int("MEMORY_TURNS", 6)
# Send retrieved figures to the model as images when it can accept them.
VISION_ENABLED = env_bool("VISION_ENABLED", True)
MAX_VISION_IMAGES = env_int("MAX_VISION_IMAGES", 4)

FETCH_TIMEOUT = env_int("FETCH_TIMEOUT", 30)

# Identify this client honestly to arXiv and Crossref, which both ask for a
# contact rather than enforcing one. Put a real address here on a public
# deployment.
USER_AGENT = env(
    "USER_AGENT",
    default="Dissect/1.0 (research paper reader; +https://github.com/)",
)
# Fetching a paper by arXiv id, DOI or URL. Off would mean the only way in is
# a file on disk, which is the single biggest friction in the tool.
FETCH_BY_URL = env_bool("FETCH_BY_URL", True)

# ---------------------------------------------------------------- behaviour

DEMO_SEED = env_bool("DEMO_SEED", False)

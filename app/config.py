"""All configuration comes from environment variables (set in Render, never in source)."""
import os
from urllib.parse import urlparse


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, "").strip()
    return v or default


def _public_base_url() -> str:
    v = _env("PUBLIC_BASE_URL")
    if v:
        return v
    ext = _env("RENDER_EXTERNAL_URL")  # provided automatically by Render
    return ext.rstrip("/") + "/a2a/" if ext else "http://localhost:8000/a2a/"


PUBLIC_BASE_URL = _public_base_url()
PREFIX = urlparse(PUBLIC_BASE_URL).path.rstrip("/")

# Provider-neutral LLM settings (any OpenAI-compatible endpoint). Old AIPIPE_* names still work.
LLM_API_KEY = _env("LLM_API_KEY") or _env("AIPIPE_TOKEN")  # secret, no default
LLM_BASE_URL = (_env("LLM_BASE_URL") or _env("AIPIPE_BASE_URL")
                or "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
LLM_MODEL = _env("LLM_MODEL", "gemini-2.5-flash")
LLM_REASONING_EFFORT = _env("LLM_REASONING_EFFORT")  # e.g. "low" for Gemini (limits hidden thinking tokens)
DB_PATH = _env("DB_PATH", "agent.db")

REQUEST_BUDGET_S = 40          # grader allows 45s per request
MAX_RESPONSE_BYTES = 480 * 1024  # spec limit is 512 KiB

CHUNK_SIZE = int(_env("CHUNK_SIZE", "12"))  # packages per model call
MAX_OUTPUT_TOKENS = int(_env("MAX_OUTPUT_TOKENS", "6000"))  # per model call (Gemini thinking tokens count)
MAX_CONCURRENCY = int(_env("MAX_CONCURRENCY", "2"))      # parallel model calls
LLM_MIN_INTERVAL = float(_env("LLM_MIN_INTERVAL", "3"))   # seconds between model calls (free-tier rate limits)

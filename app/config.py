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

AIPIPE_TOKEN = _env("AIPIPE_TOKEN")  # secret, no default
AIPIPE_BASE_URL = _env("AIPIPE_BASE_URL", "https://aipipe.org/openrouter/v1").rstrip("/")
LLM_MODEL = _env("LLM_MODEL", "openai/gpt-4.1-mini")
DB_PATH = _env("DB_PATH", "agent.db")

REQUEST_BUDGET_S = 40          # grader allows 45s per request
MAX_RESPONSE_BYTES = 480 * 1024  # spec limit is 512 KiB

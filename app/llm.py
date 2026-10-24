"""LLM access through AI Pipe (OpenAI-compatible). Token comes from the AIPIPE_TOKEN env var."""
import httpx

from . import config


async def chat(system: str, user: str, timeout: float) -> str:
    if not config.AIPIPE_TOKEN:
        raise RuntimeError("AIPIPE_TOKEN is not set")
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(
            config.AIPIPE_BASE_URL + "/chat/completions",
            headers={"Authorization": f"Bearer {config.AIPIPE_TOKEN}"},
            json={"model": config.LLM_MODEL, "temperature": 0,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

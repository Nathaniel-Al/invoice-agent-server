"""Shared constants and HTTP/A2A helpers."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import Response

MT = "application/a2a+json"
BATCH_MT = "application/vnd.ga5.invoice-claim-batch+json"
PROP_MT = "application/vnd.ga5.invoice-action-proposals+json"
RES_MT = "application/vnd.ga5.invoice-action-results+json"
RCPT_MT = "application/vnd.ga5.invoice-action-receipts+json"
ACTIONS = {"settle_invoice", "request_approval", "hold_invoice", "reject_duplicate", "open_exception"}
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED", "TASK_STATE_REJECTED"}


def canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def out(obj, code=200) -> Response:
    return Response(json.dumps(obj, ensure_ascii=False), status_code=code, media_type=MT)


def err(code, status, reason, msg) -> Response:
    return out({"error": {"code": code, "status": status, "message": msg,
                          "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                       "reason": reason, "domain": "a2a-protocol.org"}]}}, code)


_locks: dict = {}


def lock(key) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())


def preflight(request: Request):
    """Returns (principal, error_response). Each distinct Bearer token is its own principal."""
    a = request.headers.get("authorization", "")
    if not a.lower().startswith("bearer ") or not a[7:].strip():
        return None, Response(
            json.dumps({"error": {"code": 401, "status": "UNAUTHENTICATED", "message": "Authentication required"}}),
            401, media_type=MT, headers={"WWW-Authenticate": "Bearer"})
    if request.headers.get("a2a-version", "").strip() != "1.0":
        return None, err(400, "INVALID_ARGUMENT", "VERSION_NOT_SUPPORTED", "Unsupported A2A version")
    return sha(a[7:].strip()), None


async def body_json(request: Request):
    ct = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ct != MT:
        return None, err(415, "INVALID_ARGUMENT", "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/a2a+json")
    try:
        b = json.loads(await request.body())
        if not isinstance(b, dict):
            raise ValueError
        return b, None
    except Exception:
        return None, err(400, "INVALID_ARGUMENT", "INVALID_REQUEST", "Malformed JSON body")


def find_part(msg, media_type):
    for x in msg.get("parts") or []:
        if isinstance(x, dict) and x.get("mediaType") == media_type and isinstance(x.get("data"), dict):
            return x["data"]
    return None

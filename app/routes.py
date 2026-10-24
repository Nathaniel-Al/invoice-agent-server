"""A2A 1.0 HTTP+JSON routes."""
import json
import time
import uuid

from fastapi import APIRouter, Request

from . import config, store
from .decider import decide, pkg_id
from .protocol import (BATCH_MT, PROP_MT, RCPT_MT, RES_MT, TERMINAL, body_json, canon, err, find_part,
                       lock, now, out, preflight, sha)

router = APIRouter()


@router.post("/message:send")
async def send(request: Request):
    p, e = preflight(request)
    if e:
        return e
    body, e = await body_json(request)
    if e:
        return e
    msg = body.get("message")
    if (not isinstance(msg, dict) or not isinstance(msg.get("messageId"), str) or not msg["messageId"]
            or msg.get("role") != "ROLE_USER" or not isinstance(msg.get("parts"), list)):
        return err(400, "INVALID_ARGUMENT", "INVALID_REQUEST", "Invalid message")
    mid, h = msg["messageId"], sha(canon(msg))  # hash of the message only; configuration ignored
    deadline = time.monotonic() + config.REQUEST_BUDGET_S
    async with lock(("m", p, mid)):
        rec = store.message_record(p, mid)
        if rec:
            if rec[0] != h:
                return err(409, "ABORTED", "IDEMPOTENCY_CONFLICT", "Message ID reused with different content")
            t = store.load_task(p, rec[1])
            return out({"task": t}) if t else err(404, "NOT_FOUND", "TASK_NOT_FOUND", "Task not found")
        if msg.get("taskId"):
            return await _continue(p, msg, mid, h)
        return await _new_task(p, msg, mid, h, deadline)


async def _new_task(p, msg, mid, h, deadline):
    batch = find_part(msg, BATCH_MT)
    pk = batch.get("packages") if batch else None
    if (not batch or not isinstance(batch.get("batchId"), str) or not isinstance(pk, list) or not pk
            or not all(isinstance(x, dict) and pkg_id(x) for x in pk) or len({pkg_id(x) for x in pk}) != len(pk)):
        return err(400, "INVALID_ARGUMENT", "INVALID_REQUEST", "Invalid invoice batch")
    decisions = await decide(batch, deadline)
    tid, ctx = uuid.uuid4().hex, msg.get("contextId") or uuid.uuid4().hex
    props = [{"packageId": pkg_id(x), "actionId": "act_" + uuid.uuid4().hex, **d} for x, d in zip(pk, decisions)]
    task = {"id": tid, "contextId": ctx,
            "status": {"state": "TASK_STATE_INPUT_REQUIRED", "timestamp": now()},
            "artifacts": [{"artifactId": "proposals-" + uuid.uuid4().hex[:12], "name": "invoice-action-proposals",
                           "parts": [{"mediaType": PROP_MT,
                                      "data": {"batchId": batch["batchId"], "proposals": props}}]}],
            "history": [{**msg, "taskId": tid, "contextId": ctx}]}
    store.commit(p, task, mid, h)
    return out({"task": task})


async def _continue(p, msg, mid, h):
    tid = msg["taskId"]
    async with lock(("t", tid)):
        t = store.load_task(p, tid) if isinstance(tid, str) else None
        if not t:
            return err(404, "NOT_FOUND", "TASK_NOT_FOUND", "Task not found")
        bad = err(400, "INVALID_ARGUMENT", "INVALID_REQUEST", "Invalid continuation")
        if msg.get("contextId") != t["contextId"]:
            return bad
        if t["status"]["state"] in TERMINAL:
            return err(409, "ABORTED", "TASK_TERMINAL", "Task is no longer active")
        data = find_part(msg, RES_MT)
        prop = t["artifacts"][0]["parts"][0]["data"]
        if (not data or data.get("batchId") != prop["batchId"]
                or not isinstance(data.get("results"), list) or not data["results"]):
            return bad
        byid = {x["packageId"]: x for x in prop["proposals"]}
        seen, accepted = set(), {}
        for r in data["results"]:
            if not isinstance(r, dict):
                return bad
            pr = byid.get(r.get("packageId"))
            if (not pr or r["packageId"] in seen or r.get("actionId") != pr["actionId"]
                    or r.get("action") != pr["action"]):
                return bad
            seen.add(r["packageId"])
            if r.get("outcome") == "ACCEPTED":
                n = r.get("receiptNonce")
                if not isinstance(n, str) or not n:
                    return bad
                accepted[r["packageId"]] = n
            elif r.get("outcome") != "REJECTED":
                return bad
        execs = [{"packageId": x["packageId"], "actionId": x["actionId"], "action": x["action"],
                  "receiptNonce": accepted[x["packageId"]], "facts": x["facts"], "evidenceRefs": x["evidenceRefs"]}
                 for x in prop["proposals"] if x["packageId"] in accepted]
        t["artifacts"].append({"artifactId": "receipts-" + uuid.uuid4().hex[:12], "name": "invoice-action-receipts",
                               "parts": [{"mediaType": RCPT_MT,
                                          "data": {"batchId": prop["batchId"], "executions": execs}}]})
        t["history"].append({**msg, "taskId": tid, "contextId": t["contextId"]})
        t["status"] = {"state": "TASK_STATE_COMPLETED", "timestamp": now()}
        store.commit(p, t, mid, h)
        return out({"task": t})


def _view(t, hl):
    return t if hl is None else {**t, "history": t["history"][-hl:] if hl > 0 else []}


def _hl(request):
    try:
        v = request.query_params.get("historyLength")
        return max(0, int(v)) if v is not None else None
    except ValueError:
        return None


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    p, e = preflight(request)
    if e:
        return e
    t = store.load_task(p, task_id)
    return out(_view(t, _hl(request))) if t else err(404, "NOT_FOUND", "TASK_NOT_FOUND", "Task not found")


@router.get("/tasks")
async def list_tasks(request: Request):
    p, e = preflight(request)
    if e:
        return e
    tasks = store.list_tasks(p)
    try:
        n = int(request.query_params.get("pageSize", 0))
        if n > 0:
            tasks = tasks[:n]
    except ValueError:
        pass
    body = {"tasks": [_view(t, _hl(request)) for t in tasks]}
    if len(json.dumps(body)) > config.MAX_RESPONSE_BYTES:  # stay under 512 KiB
        body = {"tasks": [_view(t, 0) for t in tasks]}
        while len(json.dumps(body)) > config.MAX_RESPONSE_BYTES and body["tasks"]:
            body["tasks"].pop()
    return out(body)


@router.post("/tasks/{task_id}:cancel")
async def cancel(task_id: str, request: Request):
    p, e = preflight(request)
    if e:
        return e
    async with lock(("t", task_id)):  # same lock as result continuation: exactly one wins
        t = store.load_task(p, task_id)
        if not t:
            return err(404, "NOT_FOUND", "TASK_NOT_FOUND", "Task not found")
        if t["status"]["state"] in TERMINAL:
            return err(409, "ABORTED", "TASK_NOT_CANCELABLE", "Task is already finished")
        t["status"] = {"state": "TASK_STATE_CANCELED", "timestamp": now()}
        store.commit(p, t)
        return out(t)

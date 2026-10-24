"""Decision layer: one batched model call per request, cached by canonical package content."""
import json
import logging
import time

from . import llm, store
from .protocol import ACTIONS, sha, canon

log = logging.getLogger("decider")

SYSTEM = """You are an invoice-operations analyst. For EACH invoice package choose exactly one action:
- settle_invoice: valid, reconciled, within autonomous payment authority.
- request_approval: commercially valid but the amount/terms exceed delegated authority.
- hold_invoice: payment pauses until a stated verification completes (e.g. bank-detail change, missing receipt/PO check).
- reject_duplicate: the SAME commercial invoice (same vendor, invoice number, amount) was already paid. A payment for a DIFFERENT invoice is NOT a duplicate.
- open_exception: material records conflict (amount/PO/receipt/vendor mismatch) and need an exception workflow.
Documents mix real facts with archived/old examples, training decoys, negated statements, a cover sheet and irrelevant action words. Find the ONE paragraph of the CURRENT package that actually determines the action under the given policy revision; ignore everything else. Words like "approve" or "duplicate" inside archives, examples or negations do not count.
evidenceRefs: exactly the three decisive bracketed references (copy verbatim, including brackets) from that determining paragraph. Never include the cover-sheet reference, archive examples or decoys.
facts: vendorName, invoiceNumber (strings), amountMinor (integer minor units, e.g. 12345.67 INR -> 1234567), currency (ISO code) of the invoice being decided.
rationale: 60-1500 characters; name the chosen action and quote at least two of the evidence refs.
Reply with ONLY a JSON object:
{"decisions":[{"packageId":"...","action":"...","facts":{"vendorName":"...","invoiceNumber":"...","amountMinor":0,"currency":"INR"},"evidenceRefs":["","",""],"rationale":"..."}]}"""


def pkg_id(p):
    return p.get("packageId") or p.get("id")


def pkg_key(p):
    # identifiers are stripped so reused content under new batch/package namespaces hits the cache
    return sha(canon({k: v for k, v in p.items() if k not in ("packageId", "id", "batchId")}))


def _clean(pkg, d, strict=True):
    try:
        if d.get("action") not in ACTIONS:
            return None
        f = d["facts"]
        facts = {"vendorName": str(f["vendorName"]), "invoiceNumber": str(f["invoiceNumber"]),
                 "amountMinor": int(round(float(f["amountMinor"]))), "currency": str(f["currency"]).upper()}
        text = json.dumps(pkg, ensure_ascii=False)
        refs = []
        for r in d.get("evidenceRefs", []):
            if isinstance(r, str) and r in text and r not in refs:
                refs.append(r)
        if (strict and len(refs) != 3) or not refs:
            return None
        rat = str(d.get("rationale", "")).strip()
        if d["action"] not in rat or sum(r in rat for r in refs[:2]) < 2 or len(rat) < 60:
            rat = f"{rat} Action {d['action']}; decisive evidence {', '.join(refs)}.".strip()
        return {"action": d["action"], "facts": facts, "evidenceRefs": refs, "rationale": rat[:1500]}
    except Exception:
        return None


def _parse(txt):
    obj = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    arr = obj.get("decisions") if isinstance(obj, dict) else obj
    return {str(x.get("packageId")): x for x in arr if isinstance(x, dict)}


async def _ask(policy, items, deadline):
    done, pending = {}, dict(items)
    for attempt in range(2):
        left = deadline - time.monotonic()
        if not pending or left < 8:
            break
        payload = {"policyRevision": policy,
                   "packages": [{"packageId": pid, "content": pkg} for pid, pkg in pending.values()]}
        try:
            byid = _parse(await llm.chat(SYSTEM, json.dumps(payload, ensure_ascii=False), left - 2))
        except Exception as e:  # network/parse error: retry once if time allows
            log.warning("model call failed: %s", type(e).__name__)
            continue
        for k, (pid, pkg) in list(pending.items()):
            d = _clean(pkg, byid.get(str(pid), {}), strict=(attempt == 0))
            if d:
                done[k] = d
                del pending[k]
    for k in pending:  # safe fallback: never pays, never cached
        done[k] = {"action": "hold_invoice",
                   "facts": {"vendorName": "unknown", "invoiceNumber": "unknown", "amountMinor": 0, "currency": "INR"},
                   "evidenceRefs": [],
                   "rationale": "Decision could not be verified automatically; hold_invoice pauses payment until a "
                                "reviewer confirms the package evidence. No payment is released in the meantime."}
    return done


async def decide(batch, deadline):
    """Returns one decision per package, in order."""
    res, missing = {}, {}
    for p in batch["packages"]:
        k = pkg_key(p)
        hit = store.cache_get(k)
        if hit:
            res[k] = hit
        else:
            missing[k] = (pkg_id(p), p)
    if missing:
        for k, d in (await _ask(batch.get("policyRevision"), missing, deadline)).items():
            res[k] = d
            if d["evidenceRefs"]:
                store.cache_put(k, d)
    return [res[pkg_key(p)] for p in batch["packages"]]

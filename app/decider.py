"""Decision layer: parallel chunked model calls, cached by canonical package content."""
import asyncio
import json
import logging
import time

from . import config, llm, store
from .protocol import ACTIONS, canon, sha

log = logging.getLogger("decider")

SYSTEM = """You are a careful accounts-payable analyst. For EACH invoice package pick exactly one action.

ACTIONS
- settle_invoice: the invoice is valid, fully reconciled (invoice vs PO vs receipt/records agree), no verification pending, and the amount is within autonomous authority.
- request_approval: commercially valid and reconciled, but the amount/terms exceed the delegated approval limit stated for this package, so a higher approver must sign off.
- hold_invoice: payment must pause until a specifically stated verification completes (e.g. bank-detail change callback, tax/compliance document, vendor onboarding check).
- reject_duplicate: this SAME commercial invoice was already paid (same vendor AND same invoice number AND same amount). A payment for a DIFFERENT invoice number or different amount is NOT a duplicate, even for the same vendor.
- open_exception: material records conflict (amount, quantity, PO, receipt, vendor or currency mismatch) and need an exception workflow rather than payment.

HOW TO READ A PACKAGE
1. The package contains many paragraphs: a cover sheet, archive/old examples, training decoys, boilerplate and negated statements ("this is NOT a duplicate", "no approval was required in the past example"). Words like settle/approve/duplicate/hold in those do NOT decide anything.
2. Exactly one paragraph about the CURRENT invoice contains the controlling facts and carries three bracketed reference IDs. That paragraph decides the action. Read it literally: compare the numbers it gives (amount vs limit, invoice vs PO/receipt, prior payment vs this invoice) yourself.
3. Apply the policy revision supplied with the batch.
4. Do not default to one action. Different packages in a batch usually need different actions.

OUTPUT FIELDS
- action: one of the five above.
- facts: vendorName, invoiceNumber (strings), amountMinor (integer minor units: 12345.67 INR -> 1234567; zero-decimal currencies unchanged), currency (ISO code) for the CURRENT invoice.
- evidenceRefs: exactly the three bracketed references of the controlling paragraph, copied verbatim (include the brackets/format used). Never the cover-sheet reference, archive examples or decoys.
- why: ONE sentence, max 220 characters, stating the controlling fact that leads to the action (e.g. the amount vs limit, the matching prior payment, the mismatch, the pending verification).

Reply with ONLY this JSON object, no commentary:
{"decisions":[{"packageId":"...","action":"...","facts":{"vendorName":"...","invoiceNumber":"...","amountMinor":0,"currency":"INR"},"evidenceRefs":["","",""],"why":"..."}]}"""


def pkg_id(p):
    return p.get("packageId") or p.get("id")


def pkg_key(p):
    # identifiers stripped so reused content under new batch/package namespaces hits the cache
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
        why = " ".join(str(d.get("why") or d.get("rationale") or "").split())[:260]
        rat = f"Action {d['action']}: {why} Decisive evidence: {', '.join(refs)}."
        if len(rat) < 60:
            rat += " The cited references in the controlling paragraph support this action for the current invoice."
        return {"action": d["action"], "facts": facts, "evidenceRefs": refs, "rationale": rat[:1500]}
    except Exception:
        return None


def _parse(txt):
    obj = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    arr = obj.get("decisions") if isinstance(obj, dict) else obj
    return {str(x.get("packageId")): x for x in arr if isinstance(x, dict)}


async def _run(policy, items, deadline, strict):
    left = deadline - time.monotonic()
    if not items or left < 6:
        return {}
    payload = {"policyRevision": policy,
               "packages": [{"packageId": pid, "content": pkg} for pid, pkg in items.values()]}
    t0 = time.monotonic()
    try:
        txt = await llm.chat(SYSTEM, json.dumps(payload, ensure_ascii=False), left - 2)
        byid = _parse(txt)
    except Exception as e:
        log.warning("model call failed (%d pkgs, %.1fs): %s %s", len(items), time.monotonic() - t0,
                    type(e).__name__, str(e)[:300])
        return {}
    done = {}
    for k, (pid, pkg) in items.items():
        d = _clean(pkg, byid.get(str(pid), {}), strict)
        if d:
            done[k] = d
    log.info("model ok: %d/%d valid in %.1fs", len(done), len(items), time.monotonic() - t0)
    return done


async def _ask(policy, items, deadline):
    keys, size = list(items), max(1, config.CHUNK_SIZE)
    chunks = [{k: items[k] for k in keys[i:i + size]} for i in range(0, len(keys), size)]
    done = {}
    for r in await asyncio.gather(*[_run(policy, c, deadline, True) for c in chunks]):
        done.update(r)
    pending = {k: v for k, v in items.items() if k not in done}
    if pending:  # one lenient retry for whatever failed validation
        done.update(await _run(policy, pending, deadline, False))
    for k in items:
        if k not in done:
            log.error("FALLBACK hold_invoice used for a package")
            done[k] = {"action": "hold_invoice",
                       "facts": {"vendorName": "unknown", "invoiceNumber": "unknown",
                                 "amountMinor": 0, "currency": "INR"},
                       "evidenceRefs": [],
                       "rationale": "Action hold_invoice: the decision could not be verified automatically, so payment "
                                    "is paused until a reviewer confirms the package evidence."}
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
            if d["evidenceRefs"]:  # fallbacks are never cached
                store.cache_put(k, d)
    out = [res[pkg_key(p)] for p in batch["packages"]]
    log.info("batch actions: %s", {a: sum(o["action"] == a for o in out) for a in ACTIONS})
    return out

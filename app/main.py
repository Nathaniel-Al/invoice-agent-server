"""App entrypoint: uvicorn app.main:app"""
import logging

from fastapi import FastAPI

from . import config
from .protocol import BATCH_MT, PROP_MT, RCPT_MT, RES_MT, out
from .routes import router

logging.basicConfig(level=logging.INFO)

app = FastAPI(redirect_slashes=False, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router, prefix=config.PREFIX)


@app.get("/.well-known/agent-card.json")
async def agent_card():
    return out({
        "name": "Invoice Action Agent",
        "description": "Reads invoice packages, proposes one evidence-backed business action per invoice, and "
                       "executes only grader-accepted proposals with idempotent, user-isolated task state.",
        "version": "1.0.0",
        "supportedInterfaces": [{"url": config.PUBLIC_BASE_URL, "protocolBinding": "HTTP+JSON",
                                 "protocolVersion": "1.0"}],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}},
        "defaultInputModes": [BATCH_MT, RES_MT],
        "defaultOutputModes": [PROP_MT, RCPT_MT],
        "skills": [{"id": "invoice_action_agent", "name": "Invoice action agent",
                    "description": "Chooses settle, approve, hold, reject-duplicate or exception actions for "
                                   "invoice packages and cites the decisive evidence.",
                    "tags": ["invoice", "payments", "approval", "duplicate-detection"]}],
    })


@app.get("/healthz")
async def healthz():
    return {"ok": True}

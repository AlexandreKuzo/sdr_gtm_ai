"""API HTTP : point d'entrée webhook qui déclenche le workflow LangGraph.

Phase 3 ajoutera /slack/interactions pour reprendre le graphe après validation humaine
(actuellement, un lead qui atteint draft_email_node reste "pending" indéfiniment, faute
de mécanisme de reprise).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

import db
from agent import graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="SDR GTM Agent", lifespan=lifespan)


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    company_name: str
    company_domain: str | None = None
    # Autres champs du webhook source (Clay, etc.) acceptés sans validation stricte.


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _dedup_key(payload: dict) -> str:
    key = payload.get("company_domain") or payload.get("company_name") or ""
    return key.strip().lower()


@app.post("/webhook/lead")
def webhook_lead(payload: WebhookPayload) -> dict:
    data = payload.model_dump()
    dedup_key = _dedup_key(data)
    if not dedup_key:
        raise HTTPException(status_code=400, detail="company_name ou company_domain requis")

    existing = db.get_lead(dedup_key)
    if existing is not None and existing["status"] in ("pending", "synced"):
        return {
            "status": "skipped",
            "reason": f"lead déjà '{existing['status']}'",
            "dedup_key": dedup_key,
        }

    thread_id = dedup_key
    if existing is None:
        db.create_lead(dedup_key, thread_id)
    else:
        db.update_status(dedup_key, "pending")

    run_config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"webhook_data": data}, config=run_config)

    if "icp_score" in result:
        db.update_score(dedup_key, result["icp_score"])

    # Le graphe est "finished" s'il a atteint END (doublon/score faible, ou rejet humain
    # déjà traité). S'il reste une étape en attente (ex. interrupt_after draft_email_node),
    # le lead reste "pending" jusqu'à la validation Slack (Phase 3).
    finished = len(graph.get_state(run_config).next) == 0
    if finished:
        db.update_status(dedup_key, "synced")

    return {
        "status": "synced" if finished else "awaiting_approval",
        "thread_id": thread_id,
        "icp_score": result.get("icp_score"),
        "email_draft": result.get("email_draft"),
    }

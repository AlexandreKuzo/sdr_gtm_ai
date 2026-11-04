"""API HTTP : webhook d'entrée + callback Slack pour reprendre le graphe après
validation humaine (bouton Approve/Reject sur la notification du draft)."""
import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict

import db
import slack_client
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
        sf_lead_id = result.get("sf_status", {}).get("lead_id")
        db.update_status(dedup_key, "synced", sf_lead_id=sf_lead_id)

    return {
        "status": "synced" if finished else "awaiting_approval",
        "thread_id": thread_id,
        "icp_score": result.get("icp_score"),
        "email_draft": result.get("email_draft"),
    }


@app.post("/slack/interactions")
async def slack_interactions(request: Request) -> dict:
    raw_body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not slack_client.verify_signature(raw_body, timestamp, signature):
        raise HTTPException(status_code=401, detail="signature Slack invalide")

    form = parse_qs(raw_body.decode())
    if "payload" not in form:
        raise HTTPException(status_code=400, detail="payload Slack manquant")
    payload = json.loads(form["payload"][0])

    action = payload["actions"][0]
    thread_id = action["value"]
    approved = action["action_id"] == "approve_lead"

    lead = db.get_lead(thread_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="lead inconnu")
    if lead["status"] != "pending":
        # Déjà traité (double-clic, retry Slack) : on ne rejoue pas le graphe.
        return {"status": "ignored", "reason": f"lead déjà '{lead['status']}'"}

    run_config = {"configurable": {"thread_id": thread_id}}
    graph.update_state(run_config, {"human_approved": approved})
    result = graph.invoke(None, config=run_config)

    if "icp_score" in result:
        db.update_score(thread_id, result["icp_score"])
    sf_lead_id = result.get("sf_status", {}).get("lead_id")
    db.update_status(thread_id, "synced" if approved else "rejected", sf_lead_id=sf_lead_id)

    return {"status": "ok"}

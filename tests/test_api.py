"""Routes HTTP : authentification du webhook Clay, dédup, et reprise du graphe depuis Slack.

Les intégrations étant neutralisées par conftest.py, ces tests traversent réellement le
graphe LangGraph de bout en bout (dédup -> enrichissement -> scoring -> HITL -> brouillon
-> sync) sans appeler le moindre service externe.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import config
import db
from conftest import TEST_SECRET

BON_LEAD = {
    "company_name": "Acme Corp",
    "company_domain": "acme-test.com",
    "decision_maker_email": "vp@acme-test.com",
    "decision_maker_role": "VP of Sales",
    "company_size": "51-200",
}


def _auth():
    return {"X-Webhook-Secret": TEST_SECRET}


# ---------------------------------------------------------------- authentification
def test_webhook_refuse_sans_secret(client):
    """La route est exposée publiquement par ngrok : sans secret, rien ne passe."""
    assert client.post("/webhook/lead", json=BON_LEAD).status_code == 401


def test_webhook_refuse_un_mauvais_secret(client):
    r = client.post("/webhook/lead", json=BON_LEAD, headers={"X-Webhook-Secret": "faux"})
    assert r.status_code == 401


def test_webhook_est_ferme_si_le_secret_nest_pas_configure(client, monkeypatch):
    """Fail-closed : un .env incomplet ferme la route (503) au lieu de l'ouvrir à tous."""
    monkeypatch.setattr(config, "WEBHOOK_SECRET", None)
    r = client.post("/webhook/lead", json=BON_LEAD, headers=_auth())
    assert r.status_code == 503


def test_health_reste_public(client):
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------- contrat de payload
def test_company_name_est_obligatoire(client):
    r = client.post("/webhook/lead", json={"company_domain": "x.com"}, headers=_auth())
    assert r.status_code == 422


def test_effectif_numerique_de_clay_est_accepte(client):
    """Clay envoie l'effectif comme nombre JSON selon la source : ne doit pas faire 422."""
    payload = {**BON_LEAD, "company_size": 150, "company_domain": "numerique-test.com"}
    r = client.post("/webhook/lead", json=payload, headers=_auth())
    assert r.status_code == 200
    assert r.json()["icp_score"] == 100


def test_champs_inconnus_de_clay_sont_tolores(client):
    """Clay ajoute volontiers ses propres colonnes : on ne doit pas rejeter le lead."""
    payload = {**BON_LEAD, "clay_row_id": "abc123", "linkedin_url": "https://..."}
    assert client.post("/webhook/lead", json=payload, headers=_auth()).status_code == 200


# ---------------------------------------------------------------- parcours du lead
def test_bon_lead_sarrete_pour_validation_humaine(client):
    r = client.post("/webhook/lead", json=BON_LEAD, headers=_auth())
    body = r.json()
    assert body["status"] == "awaiting_approval"
    assert body["icp_score"] == 100
    assert body["email_draft"]
    assert db.get_lead("acme-test.com")["status"] == "pending"


def test_lead_faible_est_synchronise_sans_validation(client):
    payload = {
        "company_name": "Tiny",
        "company_domain": "tiny-test.com",
        "decision_maker_email": "stagiaire@tiny-test.com",
        "decision_maker_role": "Sales Intern",
        "company_size": "5",
    }
    body = client.post("/webhook/lead", json=payload, headers=_auth()).json()
    assert body["status"] == "synced"
    assert body["email_draft"] is None  # aucun email rédigé pour un lead hors cible


def test_lead_sans_decideur_est_logge_sans_email(client):
    payload = {"company_name": "NoContact", "company_domain": "nocontact-test.com"}
    body = client.post("/webhook/lead", json=payload, headers=_auth()).json()
    assert body["status"] == "synced"
    assert body["email_draft"] is None


def test_second_webhook_sur_le_meme_domaine_est_ignore(client):
    client.post("/webhook/lead", json=BON_LEAD, headers=_auth())
    second = client.post("/webhook/lead", json=BON_LEAD, headers=_auth()).json()
    assert second["status"] == "skipped"


def test_dedup_insensible_a_la_casse_et_aux_espaces(client):
    client.post("/webhook/lead", json=BON_LEAD, headers=_auth())
    variante = {**BON_LEAD, "company_domain": "  ACME-TEST.COM  "}
    assert client.post("/webhook/lead", json=variante, headers=_auth()).json()["status"] == "skipped"


# ---------------------------------------------------------------- callback Slack
def _slack_post(client, thread_id, action_id, signe=True):
    payload = {
        "actions": [{"action_id": action_id, "value": thread_id}],
        "response_url": None,
    }
    body = urlencode({"payload": json.dumps(payload)})
    timestamp = str(int(time.time()))
    signature = "v0=" + hmac.new(
        config.SLACK_SIGNING_SECRET.encode(),
        f"v0:{timestamp}:{body}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return client.post(
        "/slack/interactions",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature if signe else "v0=faux",
        },
    )


def test_callback_slack_non_signe_est_refuse(client):
    client.post("/webhook/lead", json=BON_LEAD, headers=_auth())
    assert _slack_post(client, "acme-test.com", "approve_lead", signe=False).status_code == 401


def test_approbation_slack_reprend_le_graphe_et_synchronise(client):
    client.post("/webhook/lead", json=BON_LEAD, headers=_auth())
    assert _slack_post(client, "acme-test.com", "approve_lead").status_code == 200
    assert db.get_lead("acme-test.com")["status"] == "synced"


def test_rejet_slack_marque_le_lead_rejete(client):
    client.post("/webhook/lead", json=BON_LEAD, headers=_auth())
    assert _slack_post(client, "acme-test.com", "reject_lead").status_code == 200
    assert db.get_lead("acme-test.com")["status"] == "rejected"


def test_double_clic_slack_ne_rejoue_pas_le_graphe(client):
    client.post("/webhook/lead", json=BON_LEAD, headers=_auth())
    _slack_post(client, "acme-test.com", "approve_lead")
    second = _slack_post(client, "acme-test.com", "approve_lead").json()
    assert second["status"] == "ignored"


def test_callback_sur_un_lead_inconnu_renvoie_404(client):
    assert _slack_post(client, "jamais-vu.com", "approve_lead").status_code == 404

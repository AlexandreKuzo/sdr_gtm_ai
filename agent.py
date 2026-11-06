"""Workflow LangGraph : webhook -> dédup Salesforce -> enrichissement -> scoring ->
notification Slack (Human-in-the-loop) -> sync Salesforce.

L'enrichissement est fait en amont par Clay, qui pousse les données déjà enrichies sur
/webhook/lead (voir enrich_lead_node). La dédup/sync Salesforce utilise sf_client.py si
SF_CLIENT_ID/SECRET/LOGIN_URL sont configurés dans .env, sinon reste en mode simulation.
Le scoring et la rédaction d'email utilisent judge.py (heuristique, ou LLM si une clé est
définie). Après validation Slack, un brouillon est créé dans la boîte Gmail du SDR
(gmail_client.py) : l'agent n'envoie jamais de lui-même.
"""
import sqlite3
from typing import Any, Dict, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

import config
import gmail_client
import judge
import sf_client
import slack_client


# ==========================================
# 1. LE STATE (L'état qui transite dans le graphe)
# ==========================================
class AgentState(TypedDict):
    # Données brutes du webhook (ex: Clay ou Signal externe)
    webhook_data: Dict[str, Any]
    # Infos enrichies (Contact, Société)
    enriched_data: Dict[str, Any]
    # Statut Salesforce (Existe déjà ou non)
    sf_status: Dict[str, Any]
    # Score ICP et raisonnement
    icp_score: int
    icp_reasoning: str
    # Message généré (objet + corps : le brouillon Gmail a besoin des deux séparément)
    email_subject: str
    email_draft: str
    # Approbation humaine (Slack)
    human_approved: bool
    # Résultat de la création du brouillon Gmail (voir gmail_client.create_draft)
    gmail_draft: Dict[str, Any]


# ==========================================
# 2. LES NŒUDS (Les fonctions de traitement)
# ==========================================
def deduplicate_salesforce_node(state: AgentState) -> Dict[str, Any]:
    """Vérifie si le lead existe déjà dans SF via le domaine de l'entreprise."""
    domain = state["webhook_data"].get("company_domain")
    company = state["webhook_data"].get("company_name")
    print(f"🔍 [Nœud 1] Vérification des doublons Salesforce pour : {company}")

    if not sf_client.is_configured():
        print("⚠️ Salesforce non configuré (.env) — dédup simulée, lead considéré unique.")
        return {"sf_status": {"is_duplicate": False, "lead_id": None}}

    if not domain:
        return {"sf_status": {"is_duplicate": False, "lead_id": None}}

    existing = sf_client.find_lead_by_domain(domain)
    if existing:
        print(f"↪️ Doublon trouvé dans Salesforce : Lead {existing['Id']}")
        return {"sf_status": {"is_duplicate": True, "lead_id": existing["Id"]}}
    return {"sf_status": {"is_duplicate": False, "lead_id": None}}


def enrich_lead_node(state: AgentState) -> Dict[str, Any]:
    """Lit les données d'enrichissement déjà fournies par Clay dans le payload du webhook.

    Clay fait l'enrichissement en amont (cascade Apollo/Clearbit/Hunter/...) et doit
    envoyer decision_maker_email, decision_maker_role et company_size via son action
    "Send Webhook" — voir WebhookPayload dans main.py pour le contrat exact. Un champ
    manquant (contact non trouvé par Clay) est accepté : le scoring heuristique dégrade
    simplement la confiance plutôt que de planter.
    """
    webhook_data = state["webhook_data"]
    enriched_data = {
        "decision_maker_email": webhook_data.get("decision_maker_email"),
        "decision_maker_role": webhook_data.get("decision_maker_role"),
        "company_size": webhook_data.get("company_size"),
    }
    print(f"🌐 [Nœud 2] Enrichissement reçu de Clay : {enriched_data}")
    return {"enriched_data": enriched_data}


def score_lead_node(state: AgentState) -> Dict[str, Any]:
    """Score le lead via judge.py (heuristique, ou LLM si ANTHROPIC_API_KEY est défini)."""
    print("📊 [Nœud 3] Scoring du lead...")
    score, reasoning = judge.score_lead(
        company_name=state["webhook_data"].get("company_name"),
        company_size=state["enriched_data"].get("company_size"),
        decision_maker_role=state["enriched_data"].get("decision_maker_role"),
    )
    return {"icp_score": score, "icp_reasoning": reasoning}


def draft_email_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Rédige l'email personnalisé (via judge.py) et notifie Slack pour validation humaine."""
    print("✍️ [Nœud 4] Rédaction de l'email de prospection...")
    subject, draft = judge.draft_email(
        decision_maker_email=state["enriched_data"]["decision_maker_email"],
        decision_maker_role=state["enriched_data"]["decision_maker_role"],
        reasoning=state["icp_reasoning"],
        company_name=state["webhook_data"].get("company_name"),
    )

    thread_id = config["configurable"]["thread_id"]
    slack_client.notify_draft(
        thread_id=thread_id,
        draft=draft,
        company_name=state["webhook_data"].get("company_name"),
        icp_score=state["icp_score"],
        icp_reasoning=state["icp_reasoning"],
    )

    return {"email_subject": subject, "email_draft": draft}


def create_gmail_draft_node(state: AgentState) -> Dict[str, Any]:
    """Crée le brouillon dans la boîte du SDR, une fois le contenu approuvé dans Slack.

    N'est atteint que par le chemin approuvé (voir route_after_human_check) : un lead
    sans contact, mal scoré, doublon ou rejeté ne produit jamais de brouillon. Un échec
    Gmail n'interrompt pas le graphe — il est remonté dans le state puis loggué dans
    l'activité Salesforce, pour que le lead ne disparaisse pas silencieusement.
    """
    print("📧 [Nœud 5] Création du brouillon Gmail...")
    result = gmail_client.create_draft(
        to=state["enriched_data"].get("decision_maker_email"),
        subject=state.get("email_subject") or "Question rapide",
        body=state.get("email_draft", ""),
    )
    return {"gmail_draft": result}


def sync_salesforce_node(state: AgentState) -> Dict[str, Any]:
    """Crée le Lead (s'il est nouveau) et loggue l'activité dans Salesforce."""
    if not sf_client.is_configured():
        print("☁️ [Nœud 6] Salesforce non configuré (.env) — synchronisation simulée.")
        return {}

    lead_id = state["sf_status"].get("lead_id")
    if lead_id is None:
        lead_id = sf_client.create_lead(
            company_name=state["webhook_data"].get("company_name"),
            company_domain=state["webhook_data"].get("company_domain"),
            decision_maker_email=state["enriched_data"].get("decision_maker_email"),
            decision_maker_role=state["enriched_data"].get("decision_maker_role"),
            icp_score=state.get("icp_score", 0),
        )
        print(f"☁️ [Nœud 6] Lead créé dans Salesforce : {lead_id}")
    else:
        print(f"☁️ [Nœud 6] Lead existant réutilisé : {lead_id}")

    subject = "Email de prospection généré" if state.get("email_draft") else "Lead loggué (doublon ou score faible)"
    description = state.get("email_draft") or state.get("icp_reasoning", "")

    # Trace du brouillon dans le CRM : sans ça, impossible de savoir depuis Salesforce
    # si l'email approuvé a bien atterri dans la boîte du SDR.
    gmail_draft = state.get("gmail_draft") or {}
    if gmail_draft.get("created"):
        description += f"\n\nBrouillon Gmail créé : {gmail_draft.get('url') or gmail_draft.get('draft_id')}"
    elif gmail_draft:
        description += f"\n\n⚠️ Brouillon Gmail non créé : {gmail_draft.get('reason')}"

    sf_client.log_activity(lead_id, subject, description)

    return {"sf_status": {**state["sf_status"], "lead_id": lead_id}}


def human_approval_gate_node(state: AgentState) -> Dict[str, Any]:
    """Nœud de passage : ne fait rien, sert uniquement de point d'arrêt (interrupt_before).

    La décision humaine ("human_approved") n'existe dans le state qu'une fois reçue de
    Slack (via graph.update_state, Phase 3) — elle ne doit donc être lue par aucun routeur
    tant que le graphe n'a pas repris après ce nœud.
    """
    return {}


# ==========================================
# 3. LES ROUTEURS (Logique conditionnelle)
# ==========================================
def route_after_scoring(state: AgentState) -> Literal["draft_email_node", "sync_salesforce_node"]:
    """Route le lead selon la note obtenue, le statut de doublon, ou l'absence de contact."""
    no_contact = not state["enriched_data"].get("decision_maker_email")
    if state["sf_status"]["is_duplicate"] or state["icp_score"] < 50 or no_contact:
        if no_contact:
            print("⚠️ Aucun décideur trouvé par Clay. Envoi direct vers Salesforce pour log.")
        else:
            print("⚠️ Lead ignoré ou doublon. Envoi direct vers Salesforce pour log/mise à jour.")
        return "sync_salesforce_node"
    return "draft_email_node"


def route_after_human_check(state: AgentState) -> Literal["create_gmail_draft_node", "__end__"]:
    """Route selon la validation humaine reçue de Slack."""
    if state["human_approved"]:
        return "create_gmail_draft_node"
    print("❌ Draft rejeté par l'humain. Fin du process.")
    return END


# ==========================================
# 4. CONSTRUCTION DU GRAPHE
# ==========================================
builder = StateGraph(AgentState)

builder.add_node("deduplicate_salesforce_node", deduplicate_salesforce_node)
builder.add_node("enrich_lead_node", enrich_lead_node)
builder.add_node("score_lead_node", score_lead_node)
builder.add_node("draft_email_node", draft_email_node)
builder.add_node("create_gmail_draft_node", create_gmail_draft_node)
builder.add_node("sync_salesforce_node", sync_salesforce_node)
builder.add_node("human_approval_gate_node", human_approval_gate_node)

builder.add_edge(START, "deduplicate_salesforce_node")
builder.add_edge("deduplicate_salesforce_node", "enrich_lead_node")
builder.add_edge("enrich_lead_node", "score_lead_node")

builder.add_conditional_edges("score_lead_node", route_after_scoring)

# Interruption Human-In-The-Loop avant d'envoyer au CRM/Email : le graphe s'arrête
# JUSTE AVANT human_approval_gate_node (interrupt_before), une fois draft_email_node
# terminé. On ne peut pas interrompre directement après draft_email_node avec un routeur
# conditionnel dessus : LangGraph évalue ce routeur dans le même "tick" que le nœud, avant
# que l'interruption ne prenne effet — donc avant que human_approved n'existe dans le state.
builder.add_edge("draft_email_node", "human_approval_gate_node")
builder.add_conditional_edges("human_approval_gate_node", route_after_human_check)
builder.add_edge("create_gmail_draft_node", "sync_salesforce_node")


def build_graph(checkpointer):
    """Compile le graphe avec le checkpointer fourni (permet d'en brancher un autre, ex. tests)."""
    return builder.compile(checkpointer=checkpointer, interrupt_before=["human_approval_gate_node"])


# Checkpointer SQLite partagé par défaut : les interruptions HITL survivent à un
# redémarrage du process (contrairement à MemorySaver).
_conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
graph = build_graph(SqliteSaver(_conn))

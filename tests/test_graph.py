"""Parcours complet du graphe, avec un checkpointer en mémoire.

Vérifie le contrat qui compte vraiment pour l'utilisateur : seul un lead approuvé par un
humain produit un brouillon dans la boîte du SDR.
"""
import pytest
from langgraph.checkpoint.memory import MemorySaver

import agent
import gmail_client

BON_LEAD = {
    "company_name": "Acme Corp",
    "company_domain": "acme.com",
    "decision_maker_email": "vp@acme.com",
    "decision_maker_role": "VP of Sales",
    "company_size": "51-200",
}


@pytest.fixture
def brouillons(monkeypatch):
    """Enregistre les appels à Gmail au lieu de les exécuter."""
    appels = []

    def _capture(to, subject, body):
        appels.append({"to": to, "subject": subject, "body": body})
        return {"created": True, "draft_id": "r-1", "url": "https://mail.google.com/x", "to": to}

    monkeypatch.setattr(gmail_client, "create_draft", _capture)
    monkeypatch.setattr(agent.slack_client, "notify_draft", lambda **kwargs: None)
    return appels


@pytest.fixture
def graph():
    return agent.build_graph(MemorySaver())


def _run(graph, payload, thread_id):
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"webhook_data": payload}, config=config)
    return result, config


def test_le_graphe_sinterrompt_avant_toute_action_irreversible(graph, brouillons):
    result, config = _run(graph, BON_LEAD, "t1")
    assert graph.get_state(config).next == ("human_approval_gate_node",)
    assert result["email_subject"] and result["email_draft"]
    assert brouillons == []  # rien n'est créé avant la validation humaine


def test_approbation_cree_le_brouillon_avec_lobjet_redige(graph, brouillons):
    _, config = _run(graph, BON_LEAD, "t2")
    graph.update_state(config, {"human_approved": True})
    result = graph.invoke(None, config=config)

    assert len(brouillons) == 1
    assert brouillons[0]["to"] == "vp@acme.com"
    assert brouillons[0]["subject"] == result["email_subject"]
    assert brouillons[0]["body"] == result["email_draft"]
    assert result["gmail_draft"]["created"] is True


def test_rejet_ne_cree_aucun_brouillon(graph, brouillons):
    _, config = _run(graph, BON_LEAD, "t3")
    graph.update_state(config, {"human_approved": False})
    graph.invoke(None, config=config)

    assert brouillons == []
    assert graph.get_state(config).next == ()  # graphe terminé


def test_lead_hors_cible_ne_passe_ni_par_slack_ni_par_gmail(graph, brouillons):
    hors_cible = {**BON_LEAD, "decision_maker_role": "Sales Intern", "company_size": "4"}
    result, config = _run(graph, hors_cible, "t4")

    assert graph.get_state(config).next == ()  # aucune interruption : pas de HITL
    assert "email_draft" not in result
    assert brouillons == []


def test_interruption_survit_a_un_redemarrage(brouillons):
    """Le checkpointer doit permettre de reprendre un lead en attente après restart."""
    checkpointer = MemorySaver()
    config = {"configurable": {"thread_id": "t5"}}

    agent.build_graph(checkpointer).invoke({"webhook_data": BON_LEAD}, config=config)

    # Nouveau graphe = process redémarré, même checkpointer persistant.
    graph_apres_restart = agent.build_graph(checkpointer)
    assert graph_apres_restart.get_state(config).next == ("human_approval_gate_node",)

    graph_apres_restart.update_state(config, {"human_approved": True})
    result = graph_apres_restart.invoke(None, config=config)
    assert result["gmail_draft"]["created"] is True
    assert len(brouillons) == 1

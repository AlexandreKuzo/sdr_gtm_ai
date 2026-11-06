"""Routeurs conditionnels du graphe : c'est là que se décide quel lead produit un email."""
from langgraph.graph import END

import agent


def _state(score=90, duplicate=False, email="vp@acme.com"):
    return {
        "webhook_data": {"company_name": "Acme", "company_domain": "acme.com"},
        "enriched_data": {"decision_maker_email": email, "decision_maker_role": "VP Sales"},
        "sf_status": {"is_duplicate": duplicate, "lead_id": None},
        "icp_score": score,
        "icp_reasoning": "peu importe",
    }


def test_bon_lead_part_en_redaction():
    assert agent.route_after_scoring(_state()) == "draft_email_node"


def test_doublon_ne_produit_pas_demail():
    assert agent.route_after_scoring(_state(duplicate=True)) == "sync_salesforce_node"


def test_score_faible_ne_produit_pas_demail():
    assert agent.route_after_scoring(_state(score=49)) == "sync_salesforce_node"


def test_seuil_de_score_a_50():
    assert agent.route_after_scoring(_state(score=50)) == "draft_email_node"


def test_sans_decideur_trouve_par_clay_on_loggue_seulement():
    assert agent.route_after_scoring(_state(email=None)) == "sync_salesforce_node"
    assert agent.route_after_scoring(_state(email="")) == "sync_salesforce_node"


def test_approbation_humaine_declenche_le_brouillon():
    assert agent.route_after_human_check({"human_approved": True}) == "create_gmail_draft_node"


def test_rejet_humain_termine_le_graphe():
    """Un rejet ne doit créer ni brouillon Gmail ni enregistrement Salesforce."""
    assert agent.route_after_human_check({"human_approved": False}) == END

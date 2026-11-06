"""Scoring et rédaction en mode heuristique (aucune clé LLM dans l'environnement de test)."""
import pytest

import judge


@pytest.mark.parametrize(
    "raw, expected",
    [
        (150, 150),              # Clay envoie parfois un nombre JSON brut
        ("150", 150),
        ("51-200", 125),         # tranche -> milieu, pas "51200"
        ("1K-5K", 3000),         # tranche abrégée
        ("10,000", 10000),       # nombre formaté
        ("500+", 500),           # borne ouverte
        ("1.5k", 1500),
        ("201 - 500 employees", 350),
        (None, None),
        ("", None),
        ("unknown", None),
        ("inconnu", None),
    ],
)
def test_parse_company_size(raw, expected):
    assert judge.parse_company_size(raw) == expected


def test_la_tranche_dans_la_cible_nest_pas_declassee():
    """Régression : "51-200" était lu comme 51200 et scoré "plus grand que la cible"."""
    score_tranche, reasoning = judge.heuristic_score("51-200", "VP Sales")
    score_exact, _ = judge.heuristic_score(125, "VP Sales")
    assert score_tranche == score_exact == 100
    assert "dans la cible ICP" in reasoning


def test_decideur_senior_vs_contact_lambda():
    senior, _ = judge.heuristic_score("150", "VP of Sales")
    junior, _ = judge.heuristic_score("150", "Sales Intern")
    assert senior > junior


def test_score_plafonne_a_100():
    score, _ = judge.heuristic_score("200", "Chief Revenue Officer")
    assert score == 100


def test_donnees_absentes_donnent_un_score_faible_sans_planter():
    score, reasoning = judge.heuristic_score(None, None)
    assert score < 50
    assert reasoning == "données insuffisantes pour un scoring fiable"


def test_entreprise_trop_grande_est_scoree_sous_la_cible():
    in_icp, _ = judge.heuristic_score("200", "VP Sales")
    too_big, reasoning = judge.heuristic_score("50000", "VP Sales")
    assert too_big < in_icp
    assert "plus grande que la cible" in reasoning


def test_draft_email_renvoie_un_objet_et_un_corps():
    subject, body = judge.draft_email(
        decision_maker_email="vp@acme.com",
        decision_maker_role="VP Sales",
        reasoning="décideur senior identifié",
        company_name="Acme",
    )
    assert "Acme" in subject
    assert "VP Sales" in body
    # L'objet ne doit pas être recollé dans le corps : Gmail les veut séparés.
    assert subject not in body

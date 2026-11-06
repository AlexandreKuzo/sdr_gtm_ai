"""Jugement du lead : scoring et rédaction d'email.

Mode heuristique par défaut (pas de dépendance externe). Si une clé LLM est définie
dans l'environnement (voir .env), bascule sur un vrai jugement : OPENAI_API_KEY en
priorité, sinon ANTHROPIC_API_KEY.
"""
import re
from typing import Optional, Tuple, Union

import config

# Capture un nombre avec suffixe d'échelle optionnel : "200", "1.5k", "10M".
_SIZE_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*([kKmM])?")
_SCALE = {"k": 1_000, "m": 1_000_000}


def parse_company_size(company_size: Union[int, float, str, None]) -> Optional[int]:
    """Normalise l'effectif envoyé par Clay en un entier représentatif.

    Selon la source utilisée dans sa cascade, Clay renvoie un nombre brut (150), une
    tranche ("51-200"), une tranche abrégée ("1K-5K"), un nombre formaté ("10,000") ou
    une borne ouverte ("500+"). Une simple extraction des chiffres collerait "51-200"
    en 51200 : on parse donc chaque borne, et pour une tranche on garde le milieu, qui
    représente l'entreprise typique de la tranche. Renvoie None si rien d'exploitable.
    """
    if company_size is None:
        return None
    if isinstance(company_size, (int, float)) and not isinstance(company_size, bool):
        return int(company_size)

    text = str(company_size).replace(",", "").replace(" ", "")
    values = []
    for number, suffix in _SIZE_TOKEN.findall(text):
        values.append(int(float(number) * _SCALE.get((suffix or "").lower(), 1)))

    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return (values[0] + values[1]) // 2  # milieu de la tranche


def heuristic_score(
    company_size: Union[int, float, str, None], decision_maker_role: Optional[str]
) -> Tuple[int, str]:
    role = (decision_maker_role or "").lower()

    score = 30
    reasons = []

    if any(k in role for k in ("vp", "chief", "head", "director", "ceo", "founder")):
        score += 40
        reasons.append("décideur senior identifié")
    elif role:
        score += 15
        reasons.append("contact identifié mais pas clairement décisionnaire")

    n = parse_company_size(company_size)
    if n is not None:
        if 50 <= n <= 500:
            score += 30
            reasons.append(f"taille d'entreprise ({n}) dans la cible ICP")
        elif n > 500:
            score += 10
            reasons.append(f"entreprise plus grande que la cible habituelle ({n})")

    score = min(score, 100)
    reasoning = "; ".join(reasons) or "données insuffisantes pour un scoring fiable"
    return score, reasoning


def _get_llm():
    """Retourne un chat model configuré (OpenAI en priorité, sinon Anthropic), ou None
    si aucune clé n'est définie — le code appelant doit alors utiliser le mode heuristique."""
    if config.OPENAI_API_KEY:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=config.OPENAI_MODEL, temperature=0)
    if config.ANTHROPIC_API_KEY:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=config.ANTHROPIC_MODEL, temperature=0)
    return None


def _llm_score(llm, company_name: str, company_size: str, decision_maker_role: str) -> Tuple[int, str]:
    from pydantic import BaseModel, Field

    class LeadScoringSchema(BaseModel):
        score: int = Field(description="Score de 0 à 100 basé sur la pertinence ICP.")
        reasoning: str = Field(description="Explication concise du score attribué.")

    structured_llm = llm.with_structured_output(LeadScoringSchema)
    prompt = (
        "Évalue ce prospect pour notre produit (logiciel B2B pour équipes Sales).\n"
        f"Société: {company_name}\n"
        f"Taille: {company_size}\n"
        f"Poste du contact: {decision_maker_role}\n"
    )
    result = structured_llm.invoke(prompt)
    return result.score, result.reasoning


def score_lead(company_name: str, company_size: str, decision_maker_role: str) -> Tuple[int, str]:
    llm = _get_llm()
    if llm is None:
        return heuristic_score(company_size, decision_maker_role)
    return _llm_score(llm, company_name, company_size, decision_maker_role)


def draft_email(decision_maker_email: str, decision_maker_role: str, reasoning: str) -> str:
    llm = _get_llm()
    if llm is None:
        return (
            "Bonjour,\n\n"
            f"En tant que {decision_maker_role}, je pense que notre solution pourrait vous "
            f"intéresser : {reasoning}.\n\n"
            "Auriez-vous 15 minutes cette semaine pour en discuter ?\n\n"
            "Cordialement"
        )

    prompt = (
        f"Rédige un email court et ultra-personnalisé à {decision_maker_email}. "
        f"Raison : {reasoning}"
    )
    return llm.invoke(prompt).content

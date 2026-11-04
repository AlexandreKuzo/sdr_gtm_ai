"""Client Salesforce : authentification (OAuth2 client_credentials) + dédup/sync des leads.

Nécessite une External Client App configurée côté org (voir check-list fournie à part)
et les variables SF_CLIENT_ID, SF_CLIENT_SECRET, SF_LOGIN_URL dans .env. Tant que ce
n'est pas fait, is_configured() renvoie False et agent.py reste en mode simulation.
"""
import time
from typing import Optional

import requests
from simple_salesforce import Salesforce

import config

_cached_client: Optional[Salesforce] = None
_cached_client_expires_at: float = 0.0


def is_configured() -> bool:
    return bool(config.SF_CLIENT_ID and config.SF_CLIENT_SECRET and config.SF_LOGIN_URL)


def _authenticate() -> tuple[str, str]:
    """Récupère un access_token via le flow OAuth2 client_credentials."""
    if config.SF_AUTH_FLOW != "client_credentials":
        raise NotImplementedError(
            f"SF_AUTH_FLOW='{config.SF_AUTH_FLOW}' non supporté pour l'instant "
            "(seul client_credentials est implémenté)."
        )

    response = requests.post(
        f"{config.SF_LOGIN_URL}/services/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": config.SF_CLIENT_ID,
            "client_secret": config.SF_CLIENT_SECRET,
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["access_token"], payload["instance_url"]


def get_client() -> Salesforce:
    """Retourne un client Salesforce authentifié, réutilisé tant que le token est valide."""
    global _cached_client, _cached_client_expires_at

    if not is_configured():
        raise RuntimeError(
            "Configuration Salesforce incomplète : SF_CLIENT_ID, SF_CLIENT_SECRET et "
            "SF_LOGIN_URL doivent être définis dans .env."
        )

    if _cached_client is not None and time.monotonic() < _cached_client_expires_at:
        return _cached_client

    access_token, instance_url = _authenticate()
    _cached_client = Salesforce(instance_url=instance_url, session_id=access_token)
    # Les tokens client_credentials Salesforce durent typiquement 2h ; on garde une marge.
    _cached_client_expires_at = time.monotonic() + 60 * 30
    return _cached_client


def find_lead_by_domain(domain: str) -> Optional[dict]:
    """Cherche un Lead existant dont le site web correspond au domaine de l'entreprise."""
    sf = get_client()
    safe_domain = domain.replace("'", "\\'")
    query = f"SELECT Id, Email, Company, Website FROM Lead WHERE Website LIKE '%{safe_domain}%' LIMIT 1"
    result = sf.query(query)
    if result["totalSize"] == 0:
        return None
    return result["records"][0]


def create_lead(
    company_name: str,
    company_domain: Optional[str],
    decision_maker_email: str,
    decision_maker_role: str,
    icp_score: int,
) -> str:
    """Crée un Lead dans Salesforce et retourne son Id.

    LastName est obligatoire côté Salesforce mais l'enrichissement (simulé pour l'instant,
    voir agent.py) ne fournit pas de nom — on le dérive de l'email en attendant une vraie
    source d'enrichissement. Website est renseigné à partir de company_domain : c'est le
    champ sur lequel find_lead_by_domain() s'appuie pour la dédup future.
    """
    sf = get_client()
    local_part = decision_maker_email.split("@")[0] if decision_maker_email else "inconnu"
    last_name = local_part.replace(".", " ").title()

    record = sf.Lead.create(
        {
            "Company": company_name,
            "Website": f"https://{company_domain}" if company_domain else None,
            "Email": decision_maker_email,
            "Title": decision_maker_role,
            "LastName": last_name,
            "LeadSource": "SDR GTM Agent",
            "Rating": "Hot" if icp_score >= 80 else "Warm",
        }
    )
    return record["id"]


def log_activity(lead_id: str, subject: str, description: str) -> None:
    """Loggue une Task liée au Lead (ex. draft d'email généré, décision humaine)."""
    sf = get_client()
    sf.Task.create(
        {
            "WhoId": lead_id,
            "Subject": subject,
            "Description": description,
            "Status": "Completed",
        }
    )

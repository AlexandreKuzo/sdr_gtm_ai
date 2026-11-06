"""Intégration Gmail : création d'un brouillon dans la boîte du SDR.

Volontairement un brouillon et pas un envoi : la validation Slack approuve le contenu,
mais c'est le SDR qui garde la gâchette finale depuis sa propre boîte. L'email part donc
de la vraie adresse du SDR (bonne délivrabilité, réponses au bon endroit) sans que l'agent
puisse envoyer quoi que ce soit tout seul.

Sans configuration Gmail dans .env, create_draft() retombe sur un print (mode simulation),
comme sf_client et slack_client — le reste du workflow continue de tourner en dev.
"""
import base64
from email.message import EmailMessage
from typing import Optional

import config

# gmail.compose suffit pour créer des brouillons : ce scope ne permet PAS de lire la
# boîte de réception, et c'est volontaire (principe du moindre privilège).
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

_cached_service = None


def is_configured() -> bool:
    return bool(
        config.GMAIL_CLIENT_ID
        and config.GMAIL_CLIENT_SECRET
        and config.GMAIL_REFRESH_TOKEN
    )


def _get_service():
    """Construit le client Gmail à partir du refresh token (obtenu une seule fois via
    scripts/gmail_authorize.py). Le refresh token ne périme pas : pas de ré-autorisation
    à chaque démarrage, contrairement à l'access token que google-auth renouvelle seul."""
    global _cached_service
    if _cached_service is not None:
        return _cached_service

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials(
        token=None,
        refresh_token=config.GMAIL_REFRESH_TOKEN,
        client_id=config.GMAIL_CLIENT_ID,
        client_secret=config.GMAIL_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    _cached_service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    return _cached_service


def _build_mime(to: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if config.GMAIL_SENDER:
        message["From"] = config.GMAIL_SENDER
    message.set_content(body)
    # L'API Gmail attend le MIME encodé en base64 *url-safe*.
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def create_draft(to: str, subject: str, body: str) -> dict:
    """Crée le brouillon et renvoie son statut.

    Le dict renvoyé est stocké dans le state du graphe et loggué dans l'activité
    Salesforce : il contient toujours une clé "created" (bool) pour que les nœuds en aval
    n'aient pas à distinguer simulation, succès et échec.
    """
    if not to:
        print("📧 [Gmail] Aucun destinataire : brouillon non créé.")
        return {"created": False, "reason": "destinataire manquant"}

    if not is_configured():
        print(
            "📧 [Gmail] Non configuré (.env) — brouillon simulé.\n"
            f"    À : {to}\n    Objet : {subject}\n"
        )
        return {"created": False, "reason": "gmail non configuré", "simulated": True}

    try:
        from googleapiclient.errors import HttpError

        service = _get_service()
        draft = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": _build_mime(to, subject, body)}})
            .execute()
        )
    except Exception as exc:  # HttpError, erreur de refresh token, réseau…
        print(f"[GMAIL:ERREUR] Échec de la création du brouillon : {exc}")
        return {"created": False, "reason": str(exc)}

    draft_id = draft.get("id")
    message_id = draft.get("message", {}).get("id")
    url = f"https://mail.google.com/mail/u/0/#drafts?compose={message_id}" if message_id else None
    print(f"📧 [Gmail] Brouillon créé pour {to} : {url or draft_id}")
    return {"created": True, "draft_id": draft_id, "url": url, "to": to}

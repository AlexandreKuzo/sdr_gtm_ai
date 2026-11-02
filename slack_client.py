"""Intégration Slack : notification du draft (boutons Approve/Reject) et vérification
de la signature des callbacks d'interaction.

Sans SLACK_BOT_TOKEN configuré, notify_draft() retombe sur un simple print (mode
simulation), pour ne pas bloquer le reste du workflow en dev.
"""
import hashlib
import hmac
import time

import config

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:  # slack_sdk non installé
    WebClient = None
    SlackApiError = Exception

_client = WebClient(token=config.SLACK_BOT_TOKEN) if (WebClient and config.SLACK_BOT_TOKEN) else None


def verify_signature(raw_body: bytes, timestamp: str, signature: str) -> bool:
    """Vérifie la signature HMAC d'une requête Slack.
    https://api.slack.com/authentication/verifying-requests-from-slack
    """
    if not config.SLACK_SIGNING_SECRET or not timestamp or not signature:
        return False

    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False  # requête trop ancienne : possible replay
    except ValueError:
        return False

    basestring = f"v0:{timestamp}:{raw_body.decode('utf-8')}".encode()
    digest = hmac.new(config.SLACK_SIGNING_SECRET.encode(), basestring, hashlib.sha256).hexdigest()
    expected_signature = f"v0={digest}"
    return hmac.compare_digest(expected_signature, signature)


def notify_draft(thread_id: str, draft: str, company_name: str, icp_score: int, icp_reasoning: str) -> None:
    """Poste le draft dans Slack avec des boutons Approve/Reject qui encodent thread_id."""
    text = (
        f"*Nouveau lead scoré — {company_name}* (score {icp_score})\n"
        f"_{icp_reasoning}_\n\n>{draft}"
    )
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "action_id": "approve_lead",
                    "value": thread_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Reject"},
                    "style": "danger",
                    "action_id": "reject_lead",
                    "value": thread_id,
                },
            ],
        },
    ]

    if _client is None:
        print(f"[SLACK:SIMULATION — SLACK_BOT_TOKEN absent du .env]\n{text}\n")
        return

    try:
        _client.chat_postMessage(channel=config.SLACK_CHANNEL, text=text, blocks=blocks)
    except SlackApiError as exc:
        print(f"[SLACK:ERREUR] Échec de l'envoi de la notification : {exc}")

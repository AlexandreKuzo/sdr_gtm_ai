"""Chargement centralisé de la configuration (variables d'environnement / .env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# --- Salesforce (voir .env, flow client_credentials par défaut) ---
SF_AUTH_FLOW = os.getenv("SF_AUTH_FLOW", "client_credentials")
SF_CLIENT_ID = os.getenv("SF_CLIENT_ID")
SF_CLIENT_SECRET = os.getenv("SF_CLIENT_SECRET")
SF_LOGIN_URL = (os.getenv("SF_LOGIN_URL") or "").rstrip("/") or None
SF_USERNAME = os.getenv("SF_USERNAME")
SF_PRIVATE_KEY_PATH = os.getenv("SF_PRIVATE_KEY_PATH")

# --- Scoring / rédaction (optionnel : sans clé, judge.py reste en mode heuristique).
# Si OPENAI_API_KEY est défini, il est utilisé en priorité ; sinon ANTHROPIC_API_KEY. ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# --- Slack (notification du draft + validation humaine via boutons Approve/Reject) ---
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "#sdr-leads")

# --- Gmail : création du brouillon dans la boîte du SDR après validation Slack.
# Le refresh token s'obtient une fois pour toutes via scripts/gmail_authorize.py. ---
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")
GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN")
# Adresse du SDR, utilisée comme en-tête From du brouillon (optionnel : Gmail met de
# toute façon l'adresse du compte autorisé).
GMAIL_SENDER = os.getenv("GMAIL_SENDER")

# --- Webhook d'entrée : secret partagé envoyé par Clay dans l'en-tête X-Webhook-Secret.
# /webhook/lead est exposé publiquement (ngrok) : sans ce secret la route est fermée
# (503) plutôt qu'ouverte, pour qu'un oubli de config ne laisse jamais la porte ouverte. ---
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# --- Persistance (SQLite pour l'instant ; migration Postgres si le volume grandit) ---
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "sdr_gtm.sqlite3"))

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

# --- Persistance (SQLite pour l'instant ; migration Postgres si le volume grandit) ---
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "sdr_gtm.sqlite3"))

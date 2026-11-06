"""Obtention du refresh token Gmail — à lancer UNE SEULE FOIS, en local.

Pré-requis côté Google Cloud :
  1. console.cloud.google.com -> créer (ou réutiliser) un projet
  2. "APIs & Services" -> Library -> activer *Gmail API*
  3. "APIs & Services" -> OAuth consent screen -> type External, ajouter ton adresse
     Gmail dans "Test users" (sinon Google refuse l'autorisation)
  4. "APIs & Services" -> Credentials -> Create credentials -> OAuth client ID
     -> type *Desktop app*
  5. Copier le Client ID / Client Secret dans .env (GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET)

Puis :
    env/bin/python scripts/gmail_authorize.py

Le navigateur s'ouvre, tu autorises avec le compte du SDR, et le script affiche le
refresh token à coller dans .env (GMAIL_REFRESH_TOKEN). Il ne périme pas.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from gmail_client import SCOPES  # noqa: E402


def main() -> int:
    if not (config.GMAIL_CLIENT_ID and config.GMAIL_CLIENT_SECRET):
        print(
            "❌ GMAIL_CLIENT_ID et GMAIL_CLIENT_SECRET doivent être dans .env avant de "
            "lancer ce script (voir la procédure en haut de ce fichier)."
        )
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow

    client_config = {
        "installed": {
            "client_id": config.GMAIL_CLIENT_ID,
            "client_secret": config.GMAIL_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    # access_type=offline + prompt=consent : indispensable pour que Google renvoie un
    # refresh token (il ne le renvoie qu'au premier consentement, sauf si on le force).
    credentials = flow.run_local_server(
        port=0, access_type="offline", prompt="consent", open_browser=True
    )

    if not credentials.refresh_token:
        print("❌ Google n'a pas renvoyé de refresh token. Relance le script.")
        return 1

    print("\n✅ Autorisation réussie. Colle cette ligne dans ton .env :\n")
    print(f"GMAIL_REFRESH_TOKEN={credentials.refresh_token}")
    print(f"GMAIL_SENDER=<l'adresse que tu viens d'autoriser>\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

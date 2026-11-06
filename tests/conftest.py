"""Isolation de l'environnement de test.

Les variables sont posées AVANT tout import du projet : config.py lit l'environnement à
l'import, et load_dotenv() n'écrase pas une variable déjà définie — c'est ce qui garantit
qu'un test ne touchera jamais le vrai Salesforce, le vrai Slack, la vraie boîte Gmail ni
la vraie base du projet, même si un .env complet est présent sur la machine.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_SECRET = "secret-de-test"

_TMP_DB = Path(tempfile.mkdtemp(prefix="sdr_gtm_tests_")) / "test.sqlite3"
os.environ["DB_PATH"] = str(_TMP_DB)
os.environ["WEBHOOK_SECRET"] = TEST_SECRET
os.environ["SLACK_SIGNING_SECRET"] = "signing-secret-de-test"
# Intégrations neutralisées : chaque client retombe sur son mode simulation.
for _var in (
    "SF_CLIENT_ID",
    "SF_CLIENT_SECRET",
    "SF_LOGIN_URL",
    "SLACK_BOT_TOKEN",
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
):
    os.environ.pop(_var, None)
    os.environ[_var] = ""

import pytest  # noqa: E402

import db  # noqa: E402


@pytest.fixture
def fresh_db():
    """Base vide avant chaque test qui touche à la persistance."""
    db.init_db()
    with db._connect() as conn:
        conn.execute("DELETE FROM leads")
    return db


@pytest.fixture
def client(fresh_db):
    """Client HTTP FastAPI sur une base vierge."""
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as c:
        yield c

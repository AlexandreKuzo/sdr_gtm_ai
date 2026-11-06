"""Création du brouillon Gmail, et garantie qu'un rejet n'en crée aucun."""
import base64
import email
import email.policy

import gmail_client


def test_non_configure_sans_identifiants():
    assert gmail_client.is_configured() is False


def test_mode_simulation_quand_gmail_nest_pas_configure():
    result = gmail_client.create_draft("vp@acme.com", "Objet", "Corps")
    assert result == {
        "created": False,
        "reason": "gmail non configuré",
        "simulated": True,
    }


def test_pas_de_brouillon_sans_destinataire():
    result = gmail_client.create_draft("", "Objet", "Corps")
    assert result["created"] is False
    assert result["reason"] == "destinataire manquant"


def test_le_mime_porte_les_bons_entetes():
    raw = gmail_client._build_mime("vp@acme.com", "Question rapide", "Bonjour,\n\nÀ bientôt")
    # policy=default : sans ça on récupère la classe Message legacy, sans get_content().
    message = email.message_from_bytes(
        base64.urlsafe_b64decode(raw), policy=email.policy.default
    )
    assert message["To"] == "vp@acme.com"
    assert message["Subject"] == "Question rapide"
    # Accents conservés après l'aller-retour base64url exigé par l'API Gmail.
    assert "À bientôt" in message.get_content()


def test_brouillon_cree_renvoie_un_lien_cliquable(monkeypatch):
    """L'URL doit pointer la fenêtre de composition, pas l'id interne du brouillon."""

    class _FakeDrafts:
        def create(self, userId, body):
            assert userId == "me"
            assert "raw" in body["message"]
            return self

        def execute(self):
            return {"id": "r-123", "message": {"id": "msg-456"}}

    class _FakeService:
        def users(self):
            return self

        def drafts(self):
            return _FakeDrafts()

    monkeypatch.setattr(gmail_client, "is_configured", lambda: True)
    monkeypatch.setattr(gmail_client, "_get_service", lambda: _FakeService())

    result = gmail_client.create_draft("vp@acme.com", "Objet", "Corps")
    assert result["created"] is True
    assert result["draft_id"] == "r-123"
    assert result["url"] == "https://mail.google.com/mail/u/0/#drafts?compose=msg-456"


def test_une_panne_gmail_ne_fait_pas_tomber_le_graphe(monkeypatch):
    """Le lead doit survivre à une erreur Gmail : l'échec remonte dans le state."""

    def _boom():
        raise RuntimeError("refresh token révoqué")

    monkeypatch.setattr(gmail_client, "is_configured", lambda: True)
    monkeypatch.setattr(gmail_client, "_get_service", _boom)

    result = gmail_client.create_draft("vp@acme.com", "Objet", "Corps")
    assert result["created"] is False
    assert "refresh token révoqué" in result["reason"]

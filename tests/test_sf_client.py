"""Échappement SOQL : le domaine vient du payload Clay, donc d'une source non fiable."""
import pytest

import sf_client


def test_non_configure_sans_identifiants():
    assert sf_client.is_configured() is False


@pytest.mark.parametrize(
    "brut, attendu",
    [
        ("acme.com", "acme.com"),
        ("acme'.com", "acme\\'.com"),                 # quote qui refermerait le littéral
        ("acme\\.com", "acme\\\\.com"),               # backslash échappé en premier
        ("acme\\'.com", "acme\\\\\\'.com"),           # la combinaison qui permettait l'injection
        ("acme%.com", "acme\\%.com"),                 # joker LIKE : élargirait la dédup
        ("acme_.com", "acme\\_.com"),
    ],
)
def test_echappement_soql(brut, attendu):
    assert sf_client.escape_soql_like(brut) == attendu


def test_le_litteral_soql_reste_ferme():
    """Une tentative d'injection ne doit pas produire de quote non échappée."""
    charge = "acme\\' OR Name LIKE '%"
    query = f"WHERE Website LIKE '%{sf_client.escape_soql_like(charge)}%'"
    # Chaque quote du littéral final est soit un délimiteur, soit précédée d'un backslash.
    quotes_non_echappees = [
        i for i, c in enumerate(query)
        if c == "'" and (i == 0 or query[i - 1] != "\\")
    ]
    assert len(quotes_non_echappees) == 2  # uniquement les deux délimiteurs

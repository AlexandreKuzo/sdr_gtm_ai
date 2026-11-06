# SDR GTM Agent

Agent de prospection sortante : Clay pousse un lead enrichi, l'agent le dédoublonne dans
Salesforce, le score sur l'ICP, rédige un email, **le fait valider par un humain dans
Slack**, puis prépare un brouillon dans la boîte Gmail du SDR et loggue tout dans le CRM.

L'agent ne prend aucune action irréversible sans validation humaine, et **n'envoie jamais
d'email lui-même** : il prépare un brouillon, le SDR garde la gâchette.

## Le flux

```
Clay (enrichissement)
   │  POST /webhook/lead  + header X-Webhook-Secret
   ▼
┌──────────────────────────┐
│ 1. dédup Salesforce      │  recherche un Lead existant sur le domaine
├──────────────────────────┤
│ 2. lecture enrichissement│  données déjà trouvées par la cascade Clay
├──────────────────────────┤
│ 3. scoring ICP           │  judge.py — heuristique, ou LLM si une clé est définie
└────────────┬─────────────┘
             │
   score ≥ 50, pas un doublon,          doublon, score < 50,
   décideur trouvé                      ou aucun décideur trouvé
             │                                   │
             ▼                                   │
┌──────────────────────────┐                     │
│ 4. rédaction + Slack     │  objet + corps, postés avec Approve / Reject
└────────────┬─────────────┘                     │
             │                                   │
      ⏸  INTERRUPTION  (le graphe attend, l'état est persisté sur disque)
             │                                   │
      Approve │  Reject ──► fin, aucune action    │
             ▼                                   │
┌──────────────────────────┐                     │
│ 5. brouillon Gmail       │  dans la boîte du SDR, jamais envoyé  │
└────────────┬─────────────┘                     │
             ▼                                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. sync Salesforce : Lead + Task (draft, lien du brouillon) │
└─────────────────────────────────────────────────────────────┘
```

L'interruption est un `interrupt_before` LangGraph adossé à un checkpointer SQLite : un
lead en attente de validation survit à un redémarrage du process. Le clic Slack arrive
plus tard sur `/slack/interactions`, qui reprend le graphe exactement où il s'était arrêté.

## Les fichiers

| Fichier | Rôle |
|---|---|
| `agent.py` | Le graphe LangGraph : nœuds, routeurs, point d'interruption |
| `main.py` | API HTTP : `/webhook/lead` (entrée Clay) et `/slack/interactions` (reprise) |
| `judge.py` | Scoring ICP et rédaction de l'email — heuristique par défaut, LLM si clé |
| `sf_client.py` | Salesforce : OAuth2 client_credentials, dédup, création de Lead, Task |
| `slack_client.py` | Notification du draft, boutons, vérification de signature |
| `gmail_client.py` | Création du brouillon dans la boîte du SDR |
| `db.py` | Table `leads` : dédup rapide et suivi de statut (distincte du checkpointer) |
| `scripts/gmail_authorize.py` | Obtention du refresh token Gmail (à lancer une fois) |

`db.py` et le checkpointer LangGraph cohabitent volontairement : le checkpointer stocke
l'état complet du graphe mais n'est pas interrogeable (« quels leads sont en attente ? »),
la table `leads` est la source de vérité métier pour la dédup et l'audit.

## Démarrer

```bash
python3 -m venv env && env/bin/pip install -r requirements.txt
cp .env.example .env     # puis remplir (voir ci-dessous)
env/bin/uvicorn main:app --reload
```

Les tests tournent hors ligne, sans aucune clé :

```bash
env/bin/python -m pytest
```

## Configuration

Chaque intégration est optionnelle et **retombe en mode simulation** si ses variables sont
absentes du `.env` — on peut donc faire tourner le workflow complet sans aucun compte.

| Variables | Sans elles |
|---|---|
| `WEBHOOK_SECRET` | ⚠️ `/webhook/lead` répond **503** (fail-closed, voir ci-dessous) |
| `SF_CLIENT_ID` / `SF_CLIENT_SECRET` / `SF_LOGIN_URL` | dédup et sync simulées |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` | le draft est imprimé en console |
| `GMAIL_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` | brouillon simulé |
| `OPENAI_API_KEY` (ou `ANTHROPIC_API_KEY`) | scoring et rédaction heuristiques |

### Sécuriser le webhook

`/webhook/lead` crée de vrais enregistrements Salesforce et est exposé publiquement (ngrok
en dev). Il est donc authentifié par un secret partagé que Clay envoie dans l'en-tête
`X-Webhook-Secret`. Le comportement est **fail-closed** : si `WEBHOOK_SECRET` est absent du
`.env`, la route répond `503` au lieu d'accepter tout le monde — un oubli de configuration
ne doit jamais ouvrir la porte.

Générer un secret :

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Brancher Clay

Sur la table d'enrichissement, ajouter une action *HTTP API* :

```
POST https://<url-ngrok>/webhook/lead

Headers
  Content-Type       application/json
  X-Webhook-Secret   <WEBHOOK_SECRET>

Body
{
  "company_name":         "{{ Company Name }}",
  "company_domain":       "{{ Domain }}",
  "decision_maker_email": "{{ Email }}",
  "decision_maker_role":  "{{ Title }}",
  "company_size":         "{{ Employee Count }}"
}
```

Seul `company_name` est obligatoire. Les colonnes supplémentaires de Clay (`clay_row_id`,
`linkedin_url`…) sont acceptées sans être validées. `company_size` est tolérant sur le
format : Clay envoie selon la source un nombre (`150`), une tranche (`"51-200"`), une
tranche abrégée (`"1K-5K"`), un nombre formaté (`"10,000"`) ou une borne (`"500+"`) — tous
sont normalisés par `judge.parse_company_size`.

### Brancher Slack

Dans la Slack App : scope `chat:write`, et *Interactivity* → Request URL pointée sur
`https://<url-ngrok>/slack/interactions`.

### Brancher Gmail

Procédure complète (projet GCP, consent screen, OAuth client) en tête de
`scripts/gmail_authorize.py`. Puis :

```bash
env/bin/python scripts/gmail_authorize.py   # une seule fois, ouvre le navigateur
```

Le scope demandé est `gmail.compose` — il permet de créer des brouillons mais **pas** de
lire la boîte de réception.

## Tester de bout en bout

```bash
curl -X POST http://localhost:8000/webhook/lead \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $(grep '^WEBHOOK_SECRET=' .env | cut -d= -f2-)" \
  -d '{"company_name":"Acme","company_domain":"acme-demo.com",
       "decision_maker_email":"vp@acme-demo.com",
       "decision_maker_role":"VP of Sales","company_size":"51-200"}'
```

Réponse attendue : `{"status":"awaiting_approval", "icp_score":100, ...}`, un message dans
Slack, et le lead en `pending` :

```bash
sqlite3 sdr_gtm.sqlite3 "SELECT dedup_key, status, icp_score, sf_lead_id FROM leads;"
```

## Statuts d'un lead

| Statut | Signification |
|---|---|
| `pending` | en attente de la validation humaine dans Slack |
| `synced` | traité : présent dans Salesforce (avec ou sans email selon le chemin) |
| `rejected` | refusé dans Slack — aucun brouillon, aucune action Salesforce |

Un second webhook sur un lead `pending` ou `synced` renvoie `skipped` sans rejouer le
graphe. La clé de dédup est le domaine (à défaut, le nom), normalisé en minuscules.

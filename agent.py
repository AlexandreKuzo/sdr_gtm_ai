from typing import Dict, Any, Literal
from typing_extensions import TypedDict
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langgraph.graph import StateMachine, StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

# ==========================================
# 1. LE STATE (L'état qui transite dans le graphe)
# ==========================================
class AgentState(TypedDict):
    # Données brutes du webhook (ex: Clay ou Signal externe)
    webhook_data: Dict[str, Any]
    # Infos enrichies (Contact, Société)
    enriched_data: Dict[str, Any]
    # Statut Salesforce (Existe déjà ou non)
    sf_status: Dict[str, Any]
    # Score ICP et raisonnement
    icp_score: int
    icp_reasoning: str
    # Message généré
    email_draft: str
    # Approbation humaine (Slack)
    human_approved: bool

# ==========================================
# 2. STRUCTURED OUTPUTS (Pour le nœud de Scoring)
# ==========================================
class LeadScoringSchema(BaseModel):
    score: int = Field(description="Score de 0 à 100 basé sur la pertinence ICP.")
    reasoning: str = Field(description="Explication concise du score attribué.")

# ==========================================
# 3. LES NŒUDS (Les fonctions de traitement)
# ==========================================
llm = ChatOpenAI(model="gpt-4o", temperature=0)

def deduplicate_salesforce_node(state: AgentState) -> Dict[str, Any]:
    """Vérifie si le lead existe déjà dans SF via email/domaine (Simulation)."""
    company = state["webhook_data"].get("company_name")
    print(f"🔍 [Nœud 1] Vérification des doublons Salesforce pour : {company}")
    
    # Ici, vous intégreriez simple-salesforce : sf.query(...)
    # Simulation : On considère que le lead est unique
    return {"sf_status": {"is_duplicate": False, "lead_id": None}}

def enrich_lead_node(state: AgentState) -> Dict[str, Any]:
    """Appelle une API (Apollo/Exa) pour trouver le décideur (Simulation)."""
    print("🌐 [Nœud 2] Enrichissement des données du prospect...")
    return {
        "enriched_data": {
            "decision_maker_email": "alex@acme.com",
            "decision_maker_role": "VP of Sales",
            "company_size": "150 employees"
        }
    }

def score_lead_node(state: AgentState) -> Dict[str, Any]:
    """Score le lead de manière structurée avec OpenAI."""
    print("📊 [Nœud 3] Scoring du lead avec GPT-4o...")
    
    structured_llm = llm.with_structured_output(LeadScoringSchema)
    
    prompt = f""" Évalue ce prospect pour notre produit (Logiciel B2B pour équipes Sales).
    Société: {state['webhook_data'].get('company_name')}
    Taille: {state['enriched_data'].get('company_size')}
    Poste du contact: {state['enriched_data'].get('decision_maker_role')}
    """
    
    result = structured_llm.invoke(prompt)
    return {"icp_score": result.score, "icp_reasoning": result.reasoning}

def draft_email_node(state: AgentState) -> Dict[str, Any]:
    """Rédige l'email personnalisé si le score est bon."""
    print("✍️ [Nœud 4] Rédaction de l'email de prospection...")
    
    prompt = f"Rédige un email court et ultra-personnalisé à {state['enriched_data']['decision_maker_email']}. Raison : {state['icp_reasoning']}"
    response = llm.invoke(prompt)
    
    # Simulation de l'envoi d'une notification Slack pour Human-In-The-Loop
    print("\n📢 [SLACK NOTIFICATION] Nouveau draft généré ! En attente de validation...")
    print(f"Draft : {response.content}\n")
    
    return {"email_draft": response.content}

def sync_salesforce_node(state: AgentState) -> Dict[str, Any]:
    """Crée le Lead et loggue l'activité dans Salesforce si approuvé."""
    print("☁️ [Nœud 5] Synchronisation finale dans Salesforce : Lead créé + Tâche logguée.")
    return {}

# ==========================================
# 4. LES ROUTEURS (Logique conditionnelle)
# ==========================================
def route_after_scoring(state: AgentState) -> Literal["draft_email_node", "sync_salesforce_node"]:
    """Route le lead selon la note obtenue ou le statut de doublon."""
    if state["sf_status"]["is_duplicate"] or state["icp_score"] < 50:
        print("⚠️ Lead ignoré ou doublon. Envoi direct vers Salesforce pour log/mise à jour.")
        return "sync_salesforce_node"
    return "draft_email_node"

def route_after_human_check(state: AgentState) -> Literal["sync_salesforce_node", "__end__"]:
    """Route selon la validation humaine reçue de Slack."""
    if state["human_approved"]:
        return "sync_salesforce_node"
    print("❌ Draft rejeté par l'humain. Fin du process.")
    return END

# ==========================================
# 5. CONSTRUCTION DU GRAPHE
# ==========================================
builder = StateGraph(AgentState)

# Ajout des nœuds
builder.add_node("deduplicate_salesforce_node", deduplicate_salesforce_node)
builder.add_node("enrich_lead_node", enrich_lead_node)
builder.add_node("score_lead_node", score_lead_node)
builder.add_node("draft_email_node", draft_email_node)
builder.add_node("sync_salesforce_node", sync_salesforce_node)

# Définition des liaisons (Edges)
builder.add_edge(START, "deduplicate_salesforce_node")
builder.add_edge("deduplicate_salesforce_node", "enrich_lead_node")
builder.add_edge("enrich_lead_node", "score_lead_node")

# Routeur conditionnel après le scoring
builder.add_conditional_edges("score_lead_node", route_after_scoring)

# Interruption Human-In-The-Loop avant d'envoyer au CRM/Email
# Le graphe va s'arrêter JUSTE APRÈS l'exécution de draft_email_node
builder.add_conditional_edges("draft_email_node", route_after_human_check)

# Configuration de la mémoire (requis pour les interruptions et le versioning d'état)
memory = MemorySaver()
graph = builder.compile(checkpointer=memory, interrupt_after=["draft_email_node"])

# src/api/routes.py

from fastapi import APIRouter, HTTPException
from src.api.models import AskRequest, AskResponse, HealthResponse
from src.agents.text_to_sql_agent import TextToSQLAgent
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

agent_instance = None

def get_agent():
    global agent_instance
    if agent_instance is None:
        agent_instance = TextToSQLAgent()
    return agent_instance

@router.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
    try:
        agent = get_agent()
        result = agent.ask(request.question, request.session_id)
        return AskResponse(**result)
    except Exception as e:
        logger.error(f"Error in /ask: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health", response_model=HealthResponse)
async def health():
    try:
        agent = get_agent()
        db_status = "Connected" if agent.db.connection else "Disconnected"
        llm_status = "Available" if agent.llm.is_available() else "Unavailable"
        return HealthResponse(
            status="ok",
            database=db_status,
            llm=llm_status
        )
    except Exception as e:
        return HealthResponse(
            status="error",
            database="Error",
            llm="Error"
        )
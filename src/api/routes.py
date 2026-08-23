# src/api/routes.py

from fastapi import APIRouter, HTTPException
from src.api.models import AskRequest, AskResponse, HealthResponse
from src.agents.text_to_sql_agent import TextToSQLAgent
import logging
import traceback

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
        
        # اگر result یک دیکشنری با success=False باشد، باز هم آن را با status 200 برمی‌گردانیم
        # تا کلاینت (Streamlit) بتواند خطا را نمایش دهد.
        return AskResponse(**result)
    
    except Exception as e:
        # لاگ کامل خطا
        logger.error(f"Unhandled error in /ask: {e}")
        logger.error(traceback.format_exc())
        
        # ساخت پاسخ خطا با success=False
        error_response = AskResponse(
            success=False,
            question=request.question,
            error=str(e),
            sql=None,
            answer=None,
            row_count=0,
            rows=[]
        )
        # بازگرداندن با status 200 تا کلاینت بتواند خطا را پردازش کند
        return error_response

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
# src/api/routes.py

from fastapi import APIRouter
from src.api.models import AskRequest, AskResponse, HealthResponse
from src.agents.text_to_sql_agent import TextToSQLAgent
import logging
from time import perf_counter

from src.utils.logger import log_event, update_session_id

router = APIRouter()

agent_instance = None

def get_agent():
    global agent_instance
    if agent_instance is None:
        agent_instance = TextToSQLAgent()
    return agent_instance

@router.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
    started = perf_counter()
    update_session_id(request.session_id)
    log_event("request_started", component="api", question_length=len(request.question))
    try:
        agent = get_agent()
        result = agent.ask(request.question, request.session_id)
        update_session_id(result.get("session_id"))
        event = "request_completed" if result.get("success") else "request_failed"
        log_event(
            event, component="api", level=logging.INFO if result.get("success") else logging.ERROR,
            duration_ms=round((perf_counter() - started) * 1000, 3),
            success=bool(result.get("success")),
        )
        
        # اگر result یک دیکشنری با success=False باشد، باز هم آن را با status 200 برمی‌گردانیم
        # تا کلاینت (Streamlit) بتواند خطا را نمایش دهد.
        return AskResponse(**result)
    
    except Exception as e:
        log_event(
            "request_failed", component="api", level=logging.ERROR,
            duration_ms=round((perf_counter() - started) * 1000, 3),
            error_type=type(e).__name__,
        )
        
        # ساخت پاسخ خطا با success=False
        error_response = AskResponse(
            success=False,
            question=request.question,
            error="Internal server error.",
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
        db_status = "On-demand execution configured"
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

# src/api/models.py

from pydantic import BaseModel
from typing import Optional, List

class AskRequest(BaseModel):
    question: str
    session_id: Optional[str] = None

class AskResponse(BaseModel):
    success: bool
    question: str
    answer: Optional[str] = None
    sql: Optional[str] = None
    row_count: Optional[int] = None
    rows: Optional[List[dict]] = None
    error: Optional[str] = None
    session_id: Optional[str] = None  # ← ضروری

class HealthResponse(BaseModel):
    status: str
    database: str
    llm: str
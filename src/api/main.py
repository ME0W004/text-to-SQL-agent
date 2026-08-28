# src/api/main.py

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from src.api.routes import router
from src.utils.logger import (
    TRACE_ID_HEADER,
    log_event,
    normalize_trace_id,
    reset_trace_context,
    set_trace_context,
)

app = FastAPI(
    title="Text-to-SQL Agent API",
    description="API for converting natural language to SQL queries",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    trace_id = normalize_trace_id(request.headers.get(TRACE_ID_HEADER))
    token = set_trace_context(trace_id)
    try:
        response = await call_next(request)
        response.headers[TRACE_ID_HEADER] = trace_id
        return response
    finally:
        reset_trace_context(token)

app.include_router(router, prefix="/api/v1")

@app.get("/")
async def root():
    return {"message": "Text-to-SQL Agent API is running. Use /api/v1/ask to query."}

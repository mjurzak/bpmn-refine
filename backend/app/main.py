from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import chat, diagrams, history, repair, validate
from app.core.config import settings

app = FastAPI(
    title="BPMN AI Validator",
    description="Interactive BPMN validation, repair, and refinement with LLMs",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(diagrams.router, prefix=settings.api_prefix)
app.include_router(validate.router, prefix=settings.api_prefix)
app.include_router(chat.router, prefix=settings.api_prefix)
app.include_router(repair.router, prefix=settings.api_prefix)
app.include_router(history.router, prefix=settings.api_prefix)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}

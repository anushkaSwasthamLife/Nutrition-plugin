"""Local chat page for trying the pipeline in a browser (development only).

Run:  .venv\\Scripts\\python.exe -m dixit_answer.web      then open http://127.0.0.1:8100
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import load_settings
from .pipeline import Engine

STATIC = Path(__file__).resolve().parent / "static"


class ChatIn(BaseModel):
    message: str = Field(..., max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


class FeedbackIn(BaseModel):
    conversation_id: str = Field(..., max_length=64)
    turn: int
    kind: str
    note: str = Field(default="", max_length=500)


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="Dixit answer pipeline (dev)")
    app.state.engine = engine

    def get_engine() -> Engine:
        if app.state.engine is None:
            app.state.engine = Engine()
        return app.state.engine

    @app.get("/")
    def index():
        return FileResponse(STATIC / "chat.html")

    @app.get("/api/info")
    def info():
        eng = get_engine()
        return {"environment": eng.settings.environment, "llm": {"provider": getattr(eng.llm, "name", "mock"),
                "model": getattr(eng.llm, "model", "")}, "versions": eng.versions, "library": eng.library.report}

    @app.post("/api/chat")
    def chat(body: ChatIn):
        result = get_engine().answer(body.message, body.conversation_id)
        return {"conversation_id": result.conversation_id, "turn": result.turn, "answer": result.answer,
                "type": result.type, "plan": result.plan, "checks": result.checks, "ai_used": result.ai_used,
                "fallback": result.fallback, "records": result.records, "ms": result.timing_ms}

    @app.post("/api/feedback")
    def feedback(body: FeedbackIn):
        try:
            get_engine().feedback(body.conversation_id, body.turn, body.kind, body.note)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/reset/{conversation_id}")
    def reset(conversation_id: str):
        get_engine().reset(conversation_id)
        return {"ok": True}

    return app


def main() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run(create_app(), host="127.0.0.1", port=settings.port)


if __name__ == "__main__":
    main()

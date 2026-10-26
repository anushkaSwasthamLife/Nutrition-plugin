"""Local chat page for trying the pipeline in a browser (development only).

Run:  .venv\\Scripts\\python.exe -m dixit_answer.web      then open http://127.0.0.1:8100

The server listens on this machine only unless DIXIT_HOST says otherwise (0.0.0.0 lets a teammate on the network open the chat).
Then the chat stays open to the network, but the dietitian review pages and their API stay on this machine: they change files.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .config import DATA_DIR, is_loopback, load_settings
from .foods import LibrarySearch
from .lists import bad_mapping_pairs
from .memory import clean_user_id, export_markdown
from .main_pipeline import MainPipeline
from .pipeline import Engine
from .review_queue import ApplyError, QueueError, ReviewQueue

STATIC = Path(__file__).resolve().parent / "static"


class ChatIn(BaseModel):
    message: str = Field(..., max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)
    profile: dict | None = None          # what the Dixit app would send: allergies, diet_type, medicines, conditions ...
    user_id: str | None = Field(default=None, max_length=64)      # who is chatting: past conversations and remembered facts hang on it
    doctor_id: str | None = Field(default=None, max_length=64)    # the patient's doctor: picks the doctor pack (default pack if unknown)


class FeedbackIn(BaseModel):
    conversation_id: str = Field(..., max_length=64)
    turn: int
    kind: str
    note: str = Field(default="", max_length=500)


class DecideIn(BaseModel):
    word: str = Field(..., max_length=80)
    kind: str = Field(..., max_length=20)
    action: str = Field(..., max_length=20)
    by: str = Field(default="", max_length=80)
    food_id: str | None = Field(default=None, max_length=80)
    name: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=40)
    allergens: list[str] | None = Field(default=None, max_length=20)
    allergens_confirmed: bool = False
    record_id: str | None = Field(default=None, max_length=80)
    aliases: list[str] | None = Field(default=None, max_length=10)
    reason: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=200)


class ReopenIn(BaseModel):
    word: str = Field(..., max_length=80)
    kind: str = Field(..., max_length=20)


class ApplyIn(BaseModel):
    by: str = Field(default="", max_length=80)


def create_app(engine: Engine | None = None, data_dir=None) -> FastAPI:
    app = FastAPI(title="Dixit answer pipeline (dev)")
    app.state.engine = engine
    app.state.main = None
    app.state.queue = None
    app.state.search = None
    data_dir = data_dir or DATA_DIR

    def get_engine() -> Engine:
        if app.state.engine is None:
            app.state.engine = Engine()
        return app.state.engine

    def get_main() -> MainPipeline:
        if app.state.main is None:
            app.state.main = MainPipeline(get_engine())
        return app.state.main

    @app.get("/")
    def index():
        return FileResponse(STATIC / "chat.html", headers={"Cache-Control": "no-cache"})     # a changed page shows on a plain reload

    @app.get("/api/info")
    def info():
        eng = get_engine()
        return {"environment": eng.settings.environment, "llm": {"provider": getattr(eng.llm, "name", "mock"),
                "model": getattr(eng.llm, "model", "")}, "versions": eng.versions, "library": eng.library.report,
                "book": eng.book.summary() if eng.book else {"status": "off"}}

    @app.post("/api/chat")
    def chat(body: ChatIn):
        result = get_main().answer(body.message, body.conversation_id, body.profile, body.user_id, body.doctor_id)
        return {"conversation_id": result.conversation_id, "turn": result.turn, "answer": result.answer,
                "type": result.type, "plan": result.plan, "checks": result.checks, "ai_used": result.ai_used,
                "fallback": result.fallback, "records": result.records, "ms": result.timing_ms, "language": result.language,
                "remembered": result.remembered, "badge": result.badge, "sources": result.sources, "route": result.route,
                "handled_by": result.handled_by}

    @app.post("/api/feedback")
    def feedback(body: FeedbackIn):
        try:
            get_engine().feedback(body.conversation_id, body.turn, body.kind, body.note)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    # ---- past conversations and what is remembered (development build; production keeps this off until consent is decided)
    def get_memory(user_id: str):
        memory = get_engine().memory
        if memory is None or clean_user_id(user_id) is None:
            raise HTTPException(status_code=404, detail="Past conversations are not switched on here.")
        return memory

    def own_turns(memory, user_id: str, conversation_id: str) -> list:
        turns = memory.turns(user_id, conversation_id)
        if not turns:
            raise HTTPException(status_code=404, detail="No such conversation.")
        return turns

    @app.get("/api/memory/{user_id}")
    def memory_summary(user_id: str):
        memory = get_memory(user_id)
        summary = memory.summary(user_id)
        names = {fid: food["name"] for fid, food in get_engine().index.foods.items()}
        for fact in summary["facts"]:                       # a remembered food shows as its name, never its id
            fact["text"] = re.sub(r"FOOD-[A-Z0-9-]+", lambda m: names.get(m.group(0), m.group(0).replace("FOOD-", "").title()), fact["text"])
        return {"conversations": memory.conversations(user_id), **summary}

    @app.get("/api/history/{user_id}/{conversation_id}")
    def history_turns(user_id: str, conversation_id: str):
        memory = get_memory(user_id)
        return {"conversation_id": conversation_id, "turns": [{"turn": t["turn"], "at": t["at"], "question": t["question"], "answer": t["answer"],
                                                               "type": t["findings"].get("type", ""), "feedback": t["feedback"]}
                                                              for t in own_turns(memory, user_id, conversation_id)]}

    @app.get("/api/history/{user_id}/{conversation_id}/export")
    def history_export(user_id: str, conversation_id: str):
        memory = get_memory(user_id)
        return PlainTextResponse(export_markdown(conversation_id, own_turns(memory, user_id, conversation_id)),
                                 headers={"Content-Disposition": 'attachment; filename="conversation.md"'})

    @app.delete("/api/history/{user_id}/{conversation_id}")
    def history_delete(user_id: str, conversation_id: str):
        memory = get_memory(user_id)
        if not memory.delete_conversation(user_id, conversation_id):
            raise HTTPException(status_code=404, detail="No such conversation.")
        get_engine().reset(conversation_id)
        return {"ok": True}

    @app.delete("/api/memory/{user_id}/fact/{key}")
    def memory_forget(user_id: str, key: str):
        return {"ok": get_memory(user_id).forget(user_id, key)}

    @app.delete("/api/memory/{user_id}")
    def memory_forget_all(user_id: str):
        engine_ = get_engine()
        for conversation_id in get_memory(user_id).forget_all(user_id):
            engine_.reset(conversation_id)
        return {"ok": True}

    @app.post("/api/reset/{conversation_id}")
    def reset(conversation_id: str):
        get_engine().reset(conversation_id)
        return {"ok": True}

    # ---- dietitian review of unknown items (plan item D). Development build: production keeps this in the app's database.
    def this_machine_only(request: Request) -> None:
        """With the server open to the network, the review pages (they change files) answer only the machine they run on."""
        if is_loopback(getattr(get_engine().settings, "host", "127.0.0.1")):
            return                                  # nobody else can connect anyway
        client = request.client.host if request.client else ""
        if not is_loopback(client):
            raise HTTPException(status_code=403, detail="The review pages can only be opened on the machine that runs this server.")

    def get_queue() -> ReviewQueue:
        eng = get_engine()
        if eng.settings.environment == "production":
            raise HTTPException(status_code=403, detail="The review page is a development tool.")
        if app.state.queue is None:
            app.state.queue = ReviewQueue(eng.settings.var_dir / "review_queue.sqlite3", eng.settings.audit_log)
        return app.state.queue

    @app.get("/review", dependencies=[Depends(this_machine_only)])
    def review_page():
        return FileResponse(STATIC / "review.html")

    @app.get("/api/review/items", dependencies=[Depends(this_machine_only)])
    def review_items():
        queue, eng = get_queue(), get_engine()
        queue.sync()
        foods = [{"id": f["id"], "name": f["name"], "category": f["category"]} for f in eng.data.food_master["foods"]]
        categories = sorted({f["category"] for f in foods})
        return {"items": queue.items(), "counts": queue.counts(), "foods": sorted(foods, key=lambda f: f["name"]),
                "categories": categories, "allergens": sorted(eng.data.allergen_map["allergens"]),
                "food_master_version": eng.data.food_master["version"]}

    @app.get("/api/review/search", dependencies=[Depends(this_machine_only)])
    def review_search(q: str = Query(..., min_length=2, max_length=60)):
        eng = get_engine()
        if app.state.search is None:
            app.state.search = LibrarySearch(eng.library, bad_mapping_pairs(eng.data.black_lists))
        hits = [h for h in app.state.search.search(q, limit=15) if not eng.library.is_indian(h["record_id"])][:8]
        out = []
        for h in hits:
            rec = eng.library.records[h["record_id"]]
            out.append({"record_id": h["record_id"], "food_name": h["food_name"], "source": rec.get("source_dataset", ""),
                        "record_type": rec.get("record_type", ""), "category": rec.get("category", ""),
                        "per_100g": rec.get("nutrients_per_100g") or {}})
        return {"results": out}

    @app.post("/api/review/decide", dependencies=[Depends(this_machine_only)])
    def review_decide(body: DecideIn):
        queue, eng = get_queue(), get_engine()
        fields = {k: v for k, v in body.model_dump().items() if k not in {"word", "kind", "action", "by"} and v not in (None, "")}
        from .foods import FoodIndex
        index = FoodIndex(eng.data.food_master, eng.words.get("spelling_map"))
        try:
            return queue.decide(body.word, body.kind, body.action, body.by, index, eng.library,
                                eng.data.allergen_map["allergens"].keys(), {f["category"] for f in eng.data.food_master["foods"]}, **fields)
        except QueueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/review/reopen", dependencies=[Depends(this_machine_only)])
    def review_reopen(body: ReopenIn):
        try:
            get_queue().reopen(body.word, body.kind)
        except QueueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/review/apply", dependencies=[Depends(this_machine_only)])
    def review_apply(body: ApplyIn):
        queue, eng = get_queue(), get_engine()
        try:
            result = queue.apply(eng, data_dir / "food_master.json", data_dir / "allergen_map.json", body.by)
        except QueueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ApplyError as exc:
            raise HTTPException(status_code=409, detail={"message": "The new food list did not pass the consistency check, so nothing was changed.",
                                                         "problems": exc.problems[:10]}) from exc
        if result["applied"]:                      # the chat starts using the new list straight away
            app.state.engine = Engine(eng.settings, llm=eng.llm)
            app.state.search = None
        return result

    return app


def network_urls(host: str, port: int) -> list[str]:
    """The addresses a teammate can type: the machine's own address when the server listens on every interface."""
    import socket

    if host not in {"0.0.0.0", "::"}:
        return [f"http://{host}:{port}"]
    try:
        addresses = sorted({info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET) if not is_loopback(info[4][0])})
    except OSError:
        addresses = []
    return [f"http://{a}:{port}" for a in addresses] or [f"http://<this machine's address>:{port}"]


def main() -> None:
    import uvicorn

    settings = load_settings()
    if not is_loopback(settings.host):
        print(f"DIXIT_HOST={settings.host}: the chat is open to anyone who can reach this machine on port {settings.port}. "
              "The review pages stay on this machine. Use it on a trusted network only.", flush=True)
        for url in network_urls(settings.host, settings.port):
            print("  teammates can open", url, flush=True)
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

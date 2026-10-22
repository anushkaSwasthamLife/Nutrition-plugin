"""Memory across conversations (22 Oct 2026).

Until now every new chat started blank: the allergy, diet, medicine and pregnancy answers were asked again, and nothing from an earlier
chat was used. This module keeps, for one person (a random id in this development page; the patient id in the Dixit app):

1. **Facts**, written by code from what the person said or the app sent, never by the AI: allergies, diet, foods avoided or at home,
   conditions, diabetes status, the six safety flags, and the last dish search. Each fact has the date it was learned.
2. **Messages**: every question and answer, with the findings for that turn (kind of answer, rules, failed checks, safety hits), so the
   chat page can show a history and a conversation can be exported.

A new chat starts from the facts. The app's own profile still wins over anything remembered. Safety flags (medicine, pregnancy, kidney,
eating disorder, under 18) and the diabetes status go stale: after `STALE_DAYS` they are not used and the chat asks again.
The person can see every fact and take any of them away, or all of them (and the messages).

Development build only: production keeps this off (`Settings.memory`) until a privacy and consent decision is made, because the facts
are health data (DPDP Act). In production the facts and messages would live in the Dixit app's database.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .state import SAFETY_FIELDS, ChatState

USER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{6,64}$")
STALE_DAYS = {"safety": 30, "diabetes_status": 90}          # days after which a remembered fact is not used (asked again)
MAX_TEXT = 4000
SAFETY_LABELS = {"diabetes_medicine": "takes diabetes medicine", "bp_medicine": "takes blood pressure medicine", "pregnancy": "pregnant",
                 "kidney_disease": "has kidney disease", "eating_disorder": "has an eating disorder", "under_18": "is under 18"}
# what makes a turn worth a second look when the conversations are reviewed
LOOK_TYPES = {"out_of_scope": "answered \"I can only help with food\"", "more_unclear": "asked what to show more of",
              "unknown_food_question": "food not on the approved list", "invalid_message": "message could not be read"}


def clean_user_id(value) -> str | None:
    """A usable person id, or None. (An id from outside is never trusted further than this: it only picks a row.)"""
    return value if isinstance(value, str) and USER_ID_RE.match(value) else None


def _day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%d %b %Y").lstrip("0")


# ---------------------------------------------------------------------------------------------------- facts <-> chat state
def collect(state: ChatState) -> dict:
    """The facts a chat state holds that are worth keeping: {key: value}. A value of "unknown" or None is not a fact."""
    facts: dict = {}
    if state.allergies is not None:
        facts["allergies"] = sorted(state.allergies)
    if state.food_preference not in {"unknown", "", None}:
        facts["food_preference"] = state.food_preference
    if state.avoid_foods:
        facts["avoid_foods"] = sorted(state.avoid_foods)
    if state.home_foods:
        facts["home_foods"] = sorted(state.home_foods)
    if state.conditions_mentioned:
        facts["conditions"] = sorted(state.conditions_mentioned)
    if state.diabetes_status not in {"unknown", "", None}:
        facts["diabetes_status"] = state.diabetes_status
    if state.prior_diabetes_history is not None:
        facts["prior_diabetes_history"] = state.prior_diabetes_history
    if state.consultant_validated is not None:
        facts["consultant_validated"] = state.consultant_validated
    for name in SAFETY_FIELDS:
        value = state.safety_status.get(name)
        if value in {"yes", "no"}:
            facts[f"safety.{name}"] = value
    if state.last_targets or state.target_include or getattr(state, "target_diet", ""):
        facts["last_search"] = {"targets": [list(t) for t in state.last_targets], "include": sorted(state.target_include),
                                "diet": state.target_diet, "exclude": sorted(state.target_exclude)}
    return facts


def is_stale(key: str, at: float, now: float) -> bool:
    kind = "safety" if key.startswith("safety.") else key
    days = STALE_DAYS.get(kind)
    return bool(days) and now - at > days * 86400


def apply_memory(state: ChatState, facts: dict, now: float | None = None) -> list[dict]:
    """Start a new chat from the remembered facts. Only what the chat does not already know is filled in. Returns what was used,
    [{"key", "label", "at"}], so the page can say "remembered from 20 Oct". Stale safety flags and diabetes status are left unknown."""
    now = now or time.time()
    used: list[dict] = []

    def use(key: str, label: str, at: float) -> None:
        used.append({"key": key, "label": label, "at": at})

    for key, item in sorted(facts.items()):
        value, at = item.get("value"), float(item.get("at") or 0)
        if key == "last_search" or is_stale(key, at, now):
            continue
        if key == "allergies" and state.allergies is None and isinstance(value, list):
            state.allergies = list(value)
            use(key, "allergies: " + (", ".join(value) if value else "none"), at)
        elif key == "food_preference" and state.food_preference == "unknown" and isinstance(value, str):
            state.food_preference = value
            use(key, f"diet: {value.replace('_', '-')}", at)
        elif key == "avoid_foods" and not state.avoid_foods and isinstance(value, list):
            state.avoid_foods = list(value)
            use(key, "does not eat: " + ", ".join(value), at)
        elif key == "home_foods" and not state.home_foods and isinstance(value, list):
            state.home_foods = list(value)
            use(key, "at home: " + ", ".join(value), at)
        elif key == "conditions" and not state.conditions_mentioned and isinstance(value, list):
            state.conditions_mentioned = list(value)
            use(key, "conditions: " + ", ".join(value), at)
        elif key == "diabetes_status" and state.diabetes_status == "unknown" and isinstance(value, str):
            state.diabetes_status = value
            use(key, f"diabetes status: {value.replace('_', ' ')}", at)
        elif key == "prior_diabetes_history" and state.prior_diabetes_history is None and isinstance(value, bool):
            state.prior_diabetes_history = value
        elif key == "consultant_validated" and state.consultant_validated is None and isinstance(value, bool):
            state.consultant_validated = value
        elif key.startswith("safety.") and value in {"yes", "no"}:
            name = key.split(".", 1)[1]
            if name in SAFETY_FIELDS and state.safety_status.get(name, "unknown") == "unknown":
                state.safety_status[name] = value
                if value == "yes":
                    use(key, SAFETY_LABELS[name], at)
    return used


def describe(key: str, value) -> str:
    """One plain line for the "What I remember about you" box."""
    if key == "allergies":
        return "Allergies: " + (", ".join(value) if value else "none")
    if key == "food_preference":
        return "Diet: " + str(value).replace("_", "-")
    if key == "avoid_foods":
        return "Does not eat: " + ", ".join(value)
    if key == "home_foods":
        return "Usually has at home: " + ", ".join(value)
    if key == "conditions":
        return "Conditions mentioned: " + ", ".join(value)
    if key == "diabetes_status":
        return "Diabetes status: " + str(value).replace("_", " ")
    if key == "prior_diabetes_history":
        return "Had diabetes before: " + ("yes" if value else "no")
    if key == "consultant_validated":
        return "Consultant has confirmed the diabetes status: " + ("yes" if value else "no")
    if key.startswith("safety."):
        name = key.split(".", 1)[1]
        return f"{SAFETY_LABELS.get(name, name).capitalize()}: {value}"
    if key == "last_search":
        return "Last dish search: " + search_label(value)
    return f"{key}: {value}"


def search_label(search: dict) -> str:
    names = {"energy_kcal": "calories", "protein_g": "protein", "carb_g": "carbs", "fat_g": "fat", "fibre_g": "fibre"}
    parts = []
    for field_name, value, mode, _high in (search.get("targets") or []):
        noun = names.get(field_name, field_name)
        parts.append({"high": f"high {noun}", "low": f"low {noun}"}.get(mode) or f"about {value:g} {noun}")
    parts += list(search.get("include") or [])
    diet = {"non_vegetarian": "non-veg", "vegetarian": "veg", "jain": "Jain", "egg": "egg"}.get(search.get("diet") or "")
    if diet:
        parts.append(diet)
    return " · ".join(parts) or "dishes"


def search_message(search: dict) -> str:
    """A message that starts the same search again, said the way a person would say it."""
    names = {"energy_kcal": "calories", "protein_g": "protein", "carb_g": "carbs", "fat_g": "fat", "fibre_g": "fibre"}
    bits = []
    for field_name, value, mode, _high in (search.get("targets") or []):
        noun = names.get(field_name, field_name)
        if mode in {"high", "low"}:
            bits.append(f"{mode} {noun}")
        else:
            bits.append(f"{value:g} {'kcal' if field_name == 'energy_kcal' else 'g ' + noun}")
    diet = {"non_vegetarian": "non veg", "vegetarian": "veg", "jain": "jain", "egg": "egg"}.get(search.get("diet") or "", "")
    include = " ".join(search.get("include") or [])
    return " ".join(x for x in [" and ".join(bits), diet, include, "dishes"] if x)


# ---------------------------------------------------------------------------------------------------- findings per turn
def findings_for(result_type: str, plan: dict, checks: list, ai_used: bool, fallback: bool, record: dict | None = None) -> dict:
    """What a reviewer wants to know about one turn, from the turn's own numbers. needs_look lists the reasons it may be a wrong answer."""
    record = record or {}
    failed = [{"id": c.get("check_id"), "level": c.get("level"), "detail": str(c.get("detail", ""))[:200]} for c in (checks or []) if not c.get("passed", True)]
    look: list[str] = []
    if result_type in LOOK_TYPES:
        look.append(LOOK_TYPES[result_type])
    if failed:
        look.append("failed check: " + ", ".join(sorted({str(f["id"]) for f in failed})))
    if fallback and result_type not in {"emergency", "harmful_intent"}:
        look.append("the plain template was sent instead of the AI wording" if ai_used is False else "fell back")
    em, harm = record.get("emergency"), record.get("harmful")
    if em and em.get("category"):
        look.append(f"emergency reply {em['category']}")
    if harm and harm.get("category"):
        look.append(f"harmful-intent reply {harm['category']}")
    return {"type": result_type, "rules": list((plan or {}).get("rules") or []), "protocol_level": (plan or {}).get("protocol_level"),
            "failed_checks": failed, "checks_run": len(checks or []), "ai_used": bool(ai_used), "fallback": bool(fallback),
            "emergency": (em or {}).get("category") if em else None, "harmful": (harm or {}).get("category") if harm else None,
            "needs_look": look}


# ---------------------------------------------------------------------------------------------------- the store
class MemoryStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS facts (user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, at REAL NOT NULL,
                                              conversation_id TEXT, PRIMARY KEY (user_id, key));
            CREATE TABLE IF NOT EXISTS turns (user_id TEXT NOT NULL, conversation_id TEXT NOT NULL, turn INTEGER NOT NULL, at REAL NOT NULL,
                                              question TEXT NOT NULL, answer TEXT NOT NULL, findings TEXT NOT NULL, feedback TEXT,
                                              PRIMARY KEY (conversation_id, turn));
            CREATE INDEX IF NOT EXISTS turns_user ON turns (user_id, at);
        """)
        self._db.commit()

    # ---- facts
    def facts(self, user_id: str) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT key, value, at, conversation_id FROM facts WHERE user_id = ?", (user_id,)).fetchall()
        out = {}
        for key, value, at, conv in rows:
            try:
                out[key] = {"value": json.loads(value), "at": at, "conversation_id": conv}
            except ValueError:
                continue
        return out

    def remember(self, user_id: str, new: dict, conversation_id: str, now: float | None = None) -> None:
        """Keep the facts that are new or changed. A fact that is unchanged keeps the date it was first learned (so staleness counts
        from then, not from the latest chat)."""
        now = now or time.time()
        old = self.facts(user_id)
        with self._lock:
            for key, value in new.items():
                if key in old and old[key]["value"] == value:
                    continue
                self._db.execute("INSERT INTO facts (user_id, key, value, at, conversation_id) VALUES (?, ?, ?, ?, ?) "
                                 "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, at = excluded.at, conversation_id = excluded.conversation_id",
                                 (user_id, key, json.dumps(value, ensure_ascii=False), now, conversation_id))
            self._db.commit()

    def forget(self, user_id: str, key: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM facts WHERE user_id = ? AND key = ?", (user_id, key))
            self._db.commit()
            return cur.rowcount > 0

    def forget_all(self, user_id: str) -> list[str]:
        """Every fact and every message of this person. Returns the conversation ids, so their chat states can be removed too."""
        with self._lock:
            ids = [r[0] for r in self._db.execute("SELECT DISTINCT conversation_id FROM turns WHERE user_id = ?", (user_id,)).fetchall()]
            self._db.execute("DELETE FROM facts WHERE user_id = ?", (user_id,))
            self._db.execute("DELETE FROM turns WHERE user_id = ?", (user_id,))
            self._db.commit()
        return ids

    def forget_search(self, user_id: str) -> None:
        self.forget(user_id, "last_search")

    # ---- messages
    def record_turn(self, user_id: str, conversation_id: str, turn: int, question: str, answer: str, findings: dict, now: float | None = None) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO turns (user_id, conversation_id, turn, at, question, answer, findings, feedback) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, (SELECT feedback FROM turns WHERE conversation_id = ? AND turn = ?))",
                             (user_id, conversation_id, turn, now or time.time(), question[:MAX_TEXT], answer[:MAX_TEXT],
                              json.dumps(findings, ensure_ascii=False), conversation_id, turn))
            self._db.commit()

    def record_feedback(self, conversation_id: str, turn: int, kind: str) -> None:
        with self._lock:
            self._db.execute("UPDATE turns SET feedback = ? WHERE conversation_id = ? AND turn = ?", (kind, conversation_id, turn))
            self._db.commit()

    def conversations(self, user_id: str) -> list[dict]:
        """One row per conversation, newest first: id, title (the first question), started, updated, turns, how many turns need a look."""
        with self._lock:
            rows = self._db.execute("SELECT conversation_id, turn, at, question, findings, feedback FROM turns WHERE user_id = ? ORDER BY conversation_id, turn",
                                    (user_id,)).fetchall()
        grouped: dict[str, dict] = {}
        for conv, turn, at, question, findings, feedback in rows:
            g = grouped.setdefault(conv, {"conversation_id": conv, "title": question[:80], "started": at, "updated": at, "turns": 0, "needs_look": 0})
            g["updated"] = max(g["updated"], at)
            g["started"] = min(g["started"], at)
            g["turns"] += 1
            try:
                look = json.loads(findings).get("needs_look")
            except ValueError:
                look = None
            if look or feedback in {"something_wrong", "not_what_i_asked", "cant_get_this"}:
                g["needs_look"] += 1
        return sorted(grouped.values(), key=lambda g: g["updated"], reverse=True)

    def turns(self, user_id: str, conversation_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT turn, at, question, answer, findings, feedback FROM turns WHERE user_id = ? AND conversation_id = ? ORDER BY turn",
                                    (user_id, conversation_id)).fetchall()
        out = []
        for turn, at, question, answer, findings, feedback in rows:
            try:
                f = json.loads(findings)
            except ValueError:
                f = {}
            out.append({"turn": turn, "at": at, "question": question, "answer": answer, "findings": f, "feedback": feedback})
        return out

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM turns WHERE user_id = ? AND conversation_id = ?", (user_id, conversation_id))
            self._db.commit()
            return cur.rowcount > 0

    def owner(self, conversation_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT user_id FROM turns WHERE conversation_id = ? LIMIT 1", (conversation_id,)).fetchone()
        return row[0] if row else None

    # ---- what the page shows
    def summary(self, user_id: str, now: float | None = None) -> dict:
        now = now or time.time()
        items = []
        suggest = None
        for key, item in sorted(self.facts(user_id).items()):
            if key == "last_search":
                if item["value"] and (item["value"].get("targets") or item["value"].get("include") or item["value"].get("diet")):
                    suggest = {"label": search_label(item["value"]), "message": search_message(item["value"]), "at": item["at"]}
                continue
            items.append({"key": key, "text": describe(key, item["value"]), "at": item["at"], "date": _day(item["at"]),
                          "stale": is_stale(key, item["at"], now)})
        return {"facts": items, "suggest": suggest}


# ---------------------------------------------------------------------------------------------------- exports
def _when(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def export_markdown(conversation_id: str, turns: list[dict]) -> str:
    looks = [(t["turn"], r) for t in turns for r in (t["findings"].get("needs_look") or [])]
    looks += [(t["turn"], f"you marked it \"{t['feedback'].replace('_', ' ')}\"") for t in turns if t["feedback"] in {"something_wrong", "not_what_i_asked", "cant_get_this"}]
    lines = [f"# Conversation {conversation_id}", "",
             f"{len(turns)} question(s), {_when(turns[0]['at']) if turns else ''} to {_when(turns[-1]['at']) if turns else ''}.", "",
             "## Needs a look", ""]
    lines += [f"- Turn {n}: {why}" for n, why in sorted(looks)] or ["- Nothing flagged."]
    lines += ["", "## Turn by turn", ""]
    for t in turns:
        f = t["findings"]
        lines += [f"### Turn {t['turn']} · {_when(t['at'])}", "", f"**Question:** {t['question']}", "", "**Answer:**", ""]
        lines += ["> " + ln if ln else ">" for ln in t["answer"].splitlines()]
        lines += ["", f"- Kind of answer: `{f.get('type', '')}`",
                  f"- Rules: {', '.join(f.get('rules') or []) or 'none'}",
                  f"- Checks run: {f.get('checks_run', 0)}; failed: {', '.join(x['id'] for x in f.get('failed_checks') or []) or 'none'}",
                  f"- AI wording: {'used' if f.get('ai_used') else 'not used'}{'; plain template sent instead' if f.get('fallback') else ''}"]
        if f.get("emergency"):
            lines.append(f"- Emergency reply: {f['emergency']}")
        if f.get("harmful"):
            lines.append(f"- Harmful-intent reply: {f['harmful']}")
        if t["feedback"]:
            lines.append(f"- Your feedback: {t['feedback'].replace('_', ' ')}")
        lines.append("")
    return "\n".join(lines)


CSV_HEADER = ["conversation_id", "turn", "time", "question", "answer", "kind_of_answer", "rules", "checks_run", "failed_checks", "ai_used", "fallback",
              "emergency", "harmful_intent", "needs_look", "feedback"]


def csv_rows(conversation_id: str, turns: list[dict]) -> list[list]:
    rows = []
    for t in turns:
        f = t["findings"]
        rows.append([conversation_id, t["turn"], _when(t["at"]), t["question"], t["answer"].replace("\n", " "), f.get("type", ""), " ".join(f.get("rules") or []),
                     f.get("checks_run", 0), " ".join(x["id"] for x in f.get("failed_checks") or [] if x.get("id")), "yes" if f.get("ai_used") else "no",
                     "yes" if f.get("fallback") else "no", f.get("emergency") or "", f.get("harmful") or "", "; ".join(f.get("needs_look") or []),
                     t["feedback"] or ""])
    return rows


__all__ = ["MemoryStore", "apply_memory", "collect", "findings_for", "clean_user_id", "export_markdown", "csv_rows", "CSV_HEADER", "describe", "STALE_DAYS"]

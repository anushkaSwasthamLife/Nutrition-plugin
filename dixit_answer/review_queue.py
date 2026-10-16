"""Unknown-item queue and dietitian review (plan item D).

Every food word the pipeline did not recognise, and every dish asked for that has no approved recipe, is written to the audit log as
an `unknown_item` record. This module turns those lines into a queue the dietitian works through:

  alias     the word is another name for a food we already have          -> added to that food's aliases
  new_food  the word is a food we should add (USDA record, category,     -> added to the food master (and the allergen map)
            allergens chosen by the reviewer)
  reject    not a food, a typo, or out of scope                            -> nothing changes
  plan      (dishes only) a recipe should be written                        -> stays on the recipe wish-list

Decisions wait in the queue until someone presses Apply. Apply builds the new food master, runs the same start-up consistency
check the engine runs, and writes the files only if that finds no problem. The chat never changes by itself.
Older audit lines (before `unknown_item` existed) are read too, from the recipe trace.
"""
from __future__ import annotations

import ast
import json
import re
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path

from .consistency import find_problems
from .foods import FoodIndex
from .textnorm import clean

ACTIONS = {"ingredient": {"alias", "new_food", "reject"}, "dish": {"plan", "reject"}}
_UNKNOWN_LIST = re.compile(r"^R1: unknown (\[.*\]),")
_UNKNOWN_ASK = re.compile(r"^R1: '(.+)' unknown, asking about (.+)$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  word TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', suggested_food_id TEXT NOT NULL DEFAULT '',
  decision TEXT NOT NULL DEFAULT '{}', decided_by TEXT NOT NULL DEFAULT '', decided_at TEXT NOT NULL DEFAULT '',
  applied_in TEXT NOT NULL DEFAULT '', PRIMARY KEY (word, kind));
CREATE TABLE IF NOT EXISTS events (
  conversation_id TEXT NOT NULL, turn INTEGER NOT NULL, word TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (conversation_id, turn, word, kind));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class QueueError(ValueError):
    """A bad request to the queue (unknown item, missing field, wrong action)."""


class ApplyError(RuntimeError):
    """Apply stopped because the new data would not pass the consistency check. Nothing was written."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:5]))
        self.problems = problems


def _key(word: str) -> str:
    return clean(str(word))[:80]


def _slug(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:40]


def _bump(version: str) -> str:
    match = re.match(r"^(.*?)(\d+)$", version or "")
    return f"{match.group(1)}{int(match.group(2)) + 1}" if match else f"{version}.1"


class ReviewQueue:
    def __init__(self, db_path: Path, audit_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path, self.audit_path = db_path, audit_path
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------------ reading the audit log
    def sync(self) -> int:
        """Read new audit lines into the queue. Safe to run any number of times. Returns the number of new events."""
        if not self.audit_path.exists():
            return 0
        with self._lock, self._connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
            offset = int(row["value"]) if row else 0
            size = self.audit_path.stat().st_size
            if size < offset:
                offset = 0
            with open(self.audit_path, "rb") as handle:
                handle.seek(offset)
                data = handle.read()
            end = data.rfind(b"\n") + 1                      # only whole lines
            added = 0
            for line in data[:end].decode("utf-8", errors="replace").splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    added += self._ingest(con, record)
            con.execute("INSERT OR REPLACE INTO meta VALUES ('offset', ?)", (str(offset + end),))
            return added

    def _event(self, con, record: dict, word: str, kind: str, suggested: str = "") -> int:
        key = _key(word)
        if not key or len(key) < 2:
            return 0
        cid, turn, at = str(record.get("conversation_id") or ""), int(record.get("turn") or 0), str(record.get("at") or "")
        cur = con.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)", (cid, turn, key, kind, at))
        con.execute("INSERT OR IGNORE INTO items (word, kind, suggested_food_id) VALUES (?,?,?)", (key, kind, suggested))
        if suggested:
            con.execute("UPDATE items SET suggested_food_id=? WHERE word=? AND kind=? AND suggested_food_id=''", (suggested, key, kind))
        return cur.rowcount

    def _ingest(self, con, record: dict) -> int:
        added = 0
        if record.get("kind") == "unknown_item":
            return self._event(con, record, record.get("word", ""), record.get("item_kind") or "ingredient",
                               str(record.get("suggested_food_id") or ""))
        if record.get("kind") != "turn":
            return 0
        for line in record.get("recipe_trace") or []:       # older lines, from before unknown_item records existed
            if not isinstance(line, str):
                continue
            match = _UNKNOWN_LIST.match(line)
            if match:
                try:
                    words = ast.literal_eval(match.group(1))
                except (ValueError, SyntaxError):
                    words = []
                for word in words if isinstance(words, list) else []:
                    added += self._event(con, record, str(word), "ingredient")
            match = _UNKNOWN_ASK.match(line)
            if match:
                added += self._event(con, record, match.group(1), "ingredient")
        dish = (record.get("plan") or {}).get("unknown_dish") if isinstance(record.get("plan"), dict) else ""
        if dish:
            added += self._event(con, record, str(dish), "dish")
        return added

    # ------------------------------------------------------------------ listing and deciding
    def items(self, status: str | None = None, kind: str | None = None) -> list[dict]:
        query = ("SELECT i.*, (SELECT COUNT(*) FROM events e WHERE e.word=i.word AND e.kind=i.kind) AS count, "
                 "(SELECT MIN(at) FROM events e WHERE e.word=i.word AND e.kind=i.kind) AS first_seen, "
                 "(SELECT MAX(at) FROM events e WHERE e.word=i.word AND e.kind=i.kind) AS last_seen FROM items i WHERE 1=1")
        args: list = []
        if status:
            query += " AND i.status=?"
            args.append(status)
        if kind:
            query += " AND i.kind=?"
            args.append(kind)
        query += " ORDER BY count DESC, last_seen DESC, i.word"
        with self._connect() as con:
            rows = con.execute(query, args).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["decision"] = json.loads(item["decision"] or "{}")
            out.append(item)
        return out

    def counts(self) -> dict:
        with self._connect() as con:
            rows = con.execute("SELECT kind, status, COUNT(*) AS n FROM items GROUP BY kind, status").fetchall()
        out: dict = {}
        for row in rows:
            out.setdefault(row["kind"], {})[row["status"]] = row["n"]
        return out

    def decide(self, word: str, kind: str, action: str, by: str, index: FoodIndex, library=None, allergen_keys=(),
               categories=(), **fields) -> dict:
        """Record a decision. Nothing in the chat changes until `apply`."""
        by = (by or "").strip()
        if len(by) < 2:
            raise QueueError("Say who is deciding (your name), so the decision can be traced.")
        if kind not in ACTIONS or action not in ACTIONS[kind]:
            raise QueueError(f"'{action}' is not an option for a {kind}.")
        key = _key(word)
        decision: dict = {"action": action}
        if action == "alias":
            food_id = fields.get("food_id")
            if food_id not in index.foods:
                raise QueueError("Choose which food this word is another name for.")
            decision["food_id"] = food_id
        elif action == "new_food":
            decision.update(self._validate_new_food(key, fields, index, library, allergen_keys, categories))
        elif action == "reject":
            reason = str(fields.get("reason") or "").strip()
            if len(reason) < 3:
                raise QueueError("Give a short reason for rejecting, for the record.")
            decision["reason"] = reason[:200]
        elif action == "plan":
            decision["note"] = str(fields.get("note") or "").strip()[:200]
        status = {"alias": "decided", "new_food": "decided", "reject": "rejected", "plan": "planned"}[action]
        with self._lock, self._connect() as con:
            row = con.execute("SELECT status FROM items WHERE word=? AND kind=?", (key, kind)).fetchone()
            if row is None:
                raise QueueError("That word is not in the queue.")
            if row["status"] == "applied":
                raise QueueError("That decision has already been applied. Edit the food list directly to change it.")
            con.execute("UPDATE items SET status=?, decision=?, decided_by=?, decided_at=? WHERE word=? AND kind=?",
                        (status, json.dumps(decision, ensure_ascii=False), by[:80], time.strftime("%Y-%m-%dT%H:%M:%S"), key, kind))
        return {"word": key, "kind": kind, "status": status, "decision": decision}

    def reopen(self, word: str, kind: str) -> None:
        with self._lock, self._connect() as con:
            cur = con.execute("UPDATE items SET status='open', decision='{}', decided_by='', decided_at='' "
                              "WHERE word=? AND kind=? AND status IN ('decided','rejected','planned')", (_key(word), kind))
            if cur.rowcount == 0:
                raise QueueError("Only a decision that has not been applied can be reopened.")

    @staticmethod
    def _validate_new_food(word: str, fields: dict, index: FoodIndex, library, allergen_keys, categories) -> dict:
        name = clean(str(fields.get("name") or word))
        if len(name) < 2:
            raise QueueError("Give the food a name.")
        category = fields.get("category")
        if category not in set(categories):
            raise QueueError("Choose a category from the list.")
        allergens = fields.get("allergens")
        if not isinstance(allergens, list) or fields.get("allergens_confirmed") is not True:
            raise QueueError("Confirm the allergens: tick the ones that apply, or confirm that it has none.")
        if any(a not in set(allergen_keys) for a in allergens):
            raise QueueError("An allergen is not one we track.")
        record = fields.get("record_id")
        if library is None or record not in library.records:
            raise QueueError("Choose the USDA record that gives this food's nutrition per 100 g.")
        if library.is_indian(record):
            raise QueueError("Choose a USDA record. Indian library records are not used for recipe maths.")
        aliases = sorted({a for a in (_key(x) for x in [word, *(fields.get("aliases") or [])]) if a and a != name})
        if index.alias_to_id.get(name) or any(index.alias_to_id.get(a) for a in aliases):
            raise QueueError("We already have a food with that name. Use 'another name for' instead.")
        return {"name": name, "category": category, "allergens": sorted(allergens), "record_id": record, "aliases": aliases}

    # ------------------------------------------------------------------ applying
    def apply(self, engine, food_master_path: Path, allergen_map_path: Path, by: str) -> dict:
        """Write every 'decided' ingredient into the food master (and allergen map), if the result passes the consistency check."""
        by = (by or "").strip()
        if len(by) < 2:
            raise QueueError("Say who is applying the changes.")
        decided = [i for i in self.items(status="decided", kind="ingredient")]
        if not decided:
            return {"applied": 0, "version": engine.data.food_master["version"], "changes": [], "skipped": []}
        old_fm_text = food_master_path.read_text(encoding="utf-8")
        old_al_text = allergen_map_path.read_text(encoding="utf-8")
        fm, al = json.loads(old_fm_text), json.loads(old_al_text)
        by_id = {f["id"]: f for f in fm["foods"]}
        taken = {_key(a): f["id"] for f in fm["foods"] for a in [f["name"], *f.get("aliases", [])]}
        changes, skipped, done = [], [], []
        new_version = _bump(fm["version"])
        for item in decided:
            word, d = item["word"], item["decision"]
            if d["action"] == "alias":
                food = by_id.get(d["food_id"])
                if food is None or (word in taken and taken[word] != d["food_id"]):
                    skipped.append({"word": word, "why": "the name is already used by another food, or the food no longer exists"})
                    continue
                if word not in taken:
                    food.setdefault("aliases", []).append(word)
                    taken[word] = food["id"]
                changes.append({"word": word, "action": "alias", "food_id": food["id"]})
            else:
                fid = f"FOOD-{_slug(d['name'])}"
                n = 2
                while fid in by_id:
                    fid, n = f"FOOD-{_slug(d['name'])}-{n}", n + 1
                names = [d["name"], *d["aliases"]]
                clashes = [x for x in names if x in taken]
                if clashes:
                    skipped.append({"word": word, "why": f"{clashes[0]} is already used by {taken[clashes[0]]}"})
                    continue
                food = {"id": fid, "name": d["name"], "category": d["category"], "aliases": sorted(set(names)),
                        "allergens": d["allergens"], "fact_records": {"development": [d["record_id"]], "production": [d["record_id"]]},
                        "ingredient_record": d["record_id"], "unit_weights_g": {}}
                fm["foods"].append(food)
                by_id[fid] = food
                for x in names:
                    taken[x] = fid
                for allergen in d["allergens"]:
                    entry = al["allergens"][allergen]
                    if fid not in entry["foods"]:
                        entry["foods"].append(fid)
                changes.append({"word": word, "action": "new_food", "food_id": fid, "name": d["name"]})
            done.append(item)
        if not changes:
            return {"applied": 0, "version": fm["version"], "changes": [], "skipped": skipped}
        fm["version"] = new_version
        fm.setdefault("review_log", []).extend(
            {"version": new_version, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "applied_by": by[:80], **c} for c in changes)
        if any(c["action"] == "new_food" for c in changes) and any(i["decision"].get("allergens") for i in done):
            al["version"] = _bump(al["version"])
        # The same check the engine runs at start-up. Anything wrong: stop, write nothing.
        index = FoodIndex(fm, engine.words.get("spelling_map"))
        problems = find_problems(replace(engine.data, food_master=fm, allergen_map=al), engine.library, index,
                                 engine.settings.environment)
        if problems:
            raise ApplyError(problems)
        try:
            food_master_path.write_text(json.dumps(fm, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            allergen_map_path.write_text(json.dumps(al, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            food_master_path.write_text(old_fm_text, encoding="utf-8")
            allergen_map_path.write_text(old_al_text, encoding="utf-8")
            raise
        with self._lock, self._connect() as con:
            for item in done:
                con.execute("UPDATE items SET status='applied', applied_in=? WHERE word=? AND kind=?", (new_version, item["word"], item["kind"]))
        return {"applied": len(changes), "version": new_version, "changes": changes, "skipped": skipped}


__all__ = ["ReviewQueue", "QueueError", "ApplyError"]

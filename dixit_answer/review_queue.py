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
import hashlib
import json
import os
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
        self._apply_lock = threading.Lock()
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
            with open(self.audit_path, "rb") as handle:
                start = hashlib.sha256(handle.read(256)).hexdigest()       # the first lines identify the file
                known = con.execute("SELECT value FROM meta WHERE key='start'").fetchone()
                if size < offset or (known and known["value"] != start):
                    offset = 0                                               # truncated or replaced: read it from the top
                handle.seek(offset)
                data = handle.read()
            con.execute("INSERT OR REPLACE INTO meta VALUES ('start', ?)", (start,))
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
        try:
            turn = int(record.get("turn") or 0)
        except (TypeError, ValueError, OverflowError):
            turn = 0
        cid, at = str(record.get("conversation_id") or "")[:80], str(record.get("at") or "")[:40]
        cur = con.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)", (cid, turn, key, kind, at))
        con.execute("INSERT OR IGNORE INTO items (word, kind, suggested_food_id) VALUES (?,?,?)", (key, kind, suggested))
        if suggested:
            con.execute("UPDATE items SET suggested_food_id=? WHERE word=? AND kind=? AND suggested_food_id=''", (suggested, key, kind))
        return cur.rowcount

    def _ingest(self, con, record: dict) -> int:
        added = 0
        if record.get("kind") == "unknown_item":
            word, item_kind = record.get("word"), record.get("item_kind")
            if not isinstance(word, str):
                return 0
            return self._event(con, record, word, item_kind if item_kind in ACTIONS else "ingredient",
                               str(record.get("suggested_food_id") or "")[:80])
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
        by = by.strip() if isinstance(by, str) else ""
        if len(by) < 2:
            raise QueueError("Say who is deciding (your name), so the decision can be traced.")
        if not isinstance(word, str) or not isinstance(kind, str) or not isinstance(action, str):
            raise QueueError("The word, kind and action must be text.")
        if kind not in ACTIONS or action not in ACTIONS[kind]:
            raise QueueError(f"'{action}' is not an option for a {kind}.")
        key = _key(word)
        decision: dict = {"action": action}
        if action == "alias":
            food_id = fields.get("food_id")
            if not isinstance(food_id, str) or food_id not in index.foods:
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
        raw_name = fields.get("name") or word
        if not isinstance(raw_name, str):
            raise QueueError("Give the food a name.")
        name = clean(raw_name)[:60]
        if len(name) < 2:
            raise QueueError("Give the food a name.")
        category = fields.get("category")
        if not isinstance(category, str) or category not in set(categories):
            raise QueueError("Choose a category from the list.")
        allergens = fields.get("allergens")
        if not isinstance(allergens, list) or fields.get("allergens_confirmed") is not True:
            raise QueueError("Confirm the allergens: tick the ones that apply, or confirm that it has none.")
        if any(not isinstance(a, str) or a not in set(allergen_keys) for a in allergens):
            raise QueueError("An allergen is not one we track.")
        allergens = sorted(set(allergens))
        record = fields.get("record_id")
        if library is None or not isinstance(record, str) or record not in library.records:
            raise QueueError("Choose the USDA record that gives this food's nutrition per 100 g.")
        if library.is_indian(record):
            raise QueueError("Choose a USDA record. Indian library records are not used for recipe maths.")
        extra = fields.get("aliases") or []
        if not isinstance(extra, list) or len(extra) > 10 or any(not isinstance(x, str) for x in extra):
            raise QueueError("Other names must be a short list of words.")
        aliases = sorted({a for a in (_key(x) for x in [word, *extra]) if a and len(a) >= 2 and a != name})
        if index.alias_to_id.get(name) or any(index.alias_to_id.get(a) for a in aliases):
            raise QueueError("We already have a food with that name. Use 'another name for' instead.")
        return {"name": name, "category": category, "allergens": allergens, "record_id": record, "aliases": aliases}

    # ------------------------------------------------------------------ applying
    def _note(self, word: str, kind: str, text: str) -> None:
        """Keep the reason an item could not be applied with the item, so the reviewer sees it on the page."""
        with self._lock, self._connect() as con:
            row = con.execute("SELECT decision FROM items WHERE word=? AND kind=?", (word, kind)).fetchone()
            if row:
                decision = json.loads(row["decision"] or "{}")
                if text:
                    decision["not_applied_because"] = text
                else:
                    decision.pop("not_applied_because", None)
                con.execute("UPDATE items SET decision=? WHERE word=? AND kind=?", (json.dumps(decision, ensure_ascii=False), word, kind))

    @staticmethod
    def _write_atomically(path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()                                      # nothing is left behind if the replace failed
            except FileNotFoundError:
                pass

    def apply(self, engine, food_master_path: Path, allergen_map_path: Path, by: str) -> dict:
        """Write every 'decided' ingredient into the food master (and allergen map), one at a time, each only if the result passes
        the engine's start-up consistency check. An item that would break the check is skipped with its reason (kept on the item);
        the others still go in. Nothing is written when no item passes."""
        by = by.strip() if isinstance(by, str) else ""
        if len(by) < 2:
            raise QueueError("Say who is applying the changes.")
        with self._apply_lock:
            return self._apply(engine, food_master_path, allergen_map_path, by)

    def _apply(self, engine, food_master_path: Path, allergen_map_path: Path, by: str) -> dict:
        decided = self.items(status="decided", kind="ingredient")
        if not decided:
            return {"applied": 0, "version": engine.data.food_master["version"], "changes": [], "skipped": []}
        try:
            old_fm_text = food_master_path.read_text(encoding="utf-8")
            old_al_text = allergen_map_path.read_text(encoding="utf-8")
            fm, al = json.loads(old_fm_text), json.loads(old_al_text)
            if not isinstance(fm, dict) or not isinstance(al, dict) or not isinstance(fm.get("foods"), list) \
                    or not isinstance(al.get("allergens"), dict) or not isinstance(fm.get("version"), str):
                raise ValueError("unexpected layout")
        except (OSError, ValueError) as exc:
            raise ApplyError([f"the food list or allergen map cannot be read ({exc.__class__.__name__}). Nothing was changed."]) from exc
        by_id = {f["id"]: f for f in fm["foods"]}
        taken = {_key(a): f["id"] for f in fm["foods"] for a in [f["name"], *f.get("aliases", [])]}
        new_version = _bump(fm["version"])
        spelling = engine.words.get("spelling_map")

        def problems_of(foods_doc, allergen_doc) -> set[str]:
            index = FoodIndex(foods_doc, spelling)
            return set(find_problems(replace(engine.data, food_master=foods_doc, allergen_map=allergen_doc), engine.library, index,
                                     engine.settings.environment))

        baseline = problems_of(fm, al)
        changes, skipped, done = [], [], []
        for item in decided:
            word, d = item["word"], item["decision"]
            trial_fm, trial_al = json.loads(json.dumps(fm)), json.loads(json.dumps(al))
            t_by_id, t_taken = {f["id"]: f for f in trial_fm["foods"]}, dict(taken)
            reason = ""
            try:
                if d.get("action") == "alias":
                    food = t_by_id.get(d.get("food_id"))
                    if food is None:
                        reason = "the food it was matched to no longer exists"
                    elif t_taken.get(word, food["id"]) != food["id"]:
                        reason = f"the name is already used by {t_taken[word]}"
                    elif word not in t_taken:
                        food.setdefault("aliases", []).append(word)
                        t_taken[word] = food["id"]
                    change = {"word": word, "action": "alias", "food_id": d.get("food_id")}
                else:
                    fid = f"FOOD-{_slug(d['name'])}"
                    n = 2
                    while fid in t_by_id:
                        fid, n = f"FOOD-{_slug(d['name'])}-{n}", n + 1
                    names = [d["name"], *d["aliases"]]
                    clash = next((x for x in names if x in t_taken), None)
                    missing_allergen = next((a for a in d["allergens"] if a not in trial_al["allergens"]), None)
                    if clash:
                        reason = f"the name {clash} is already used by {t_taken[clash]}"
                    elif missing_allergen:
                        reason = f"the allergen {missing_allergen} is no longer in the allergen map"
                    else:
                        trial_fm["foods"].append({
                            "id": fid, "name": d["name"], "category": d["category"], "aliases": sorted(set(names)), "allergens": d["allergens"],
                            "fact_records": {"development": [d["record_id"]], "production": [d["record_id"]]},
                            "ingredient_record": d["record_id"], "unit_weights_g": {}})
                        for x in names:
                            t_taken[x] = fid
                        for allergen in d["allergens"]:
                            if fid not in trial_al["allergens"][allergen]["foods"]:
                                trial_al["allergens"][allergen]["foods"].append(fid)
                    change = {"word": word, "action": "new_food", "food_id": fid, "name": d.get("name")}
            except (KeyError, TypeError, AttributeError) as exc:
                reason, change = f"the decision is incomplete ({exc.__class__.__name__})", {}
            if not reason:
                new_problems = problems_of(trial_fm, trial_al) - baseline
                if new_problems:
                    reason = "it would break the consistency check: " + "; ".join(sorted(new_problems)[:2])
            if reason:
                skipped.append({"word": word, "why": reason})
                self._note(word, item["kind"], reason)
                continue
            fm, al, by_id, taken = trial_fm, trial_al, {f["id"]: f for f in trial_fm["foods"]}, t_taken
            changes.append(change)
            done.append(item)
        if not changes:
            return {"applied": 0, "version": fm["version"], "changes": [], "skipped": skipped}
        fm["version"] = new_version
        fm.setdefault("review_log", []).extend({"version": new_version, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "applied_by": by[:80], **c}
                                                for c in changes)
        if any(i["decision"].get("allergens") for i in done if i["decision"].get("action") == "new_food"):
            al["version"] = _bump(al["version"])
        if problems_of(fm, al) - baseline:                               # last look at the finished result
            raise ApplyError(sorted(problems_of(fm, al) - baseline))
        try:
            self._write_atomically(food_master_path, json.dumps(fm, indent=2, ensure_ascii=False) + "\n")
            self._write_atomically(allergen_map_path, json.dumps(al, indent=2, ensure_ascii=False) + "\n")
        except OSError as exc:
            for path, text in ((food_master_path, old_fm_text), (allergen_map_path, old_al_text)):
                try:
                    self._write_atomically(path, text)
                except OSError:
                    pass
            raise ApplyError([f"the files could not be written ({exc.__class__.__name__}). The earlier food list was put back."]) from exc
        with self._lock, self._connect() as con:
            for item in done:
                con.execute("UPDATE items SET status='applied', applied_in=? WHERE word=? AND kind=?", (new_version, item["word"], item["kind"]))
        for item in done:
            self._note(item["word"], item["kind"], "")
        return {"applied": len(changes), "version": new_version, "changes": changes, "skipped": skipped}


__all__ = ["ReviewQueue", "QueueError", "ApplyError"]

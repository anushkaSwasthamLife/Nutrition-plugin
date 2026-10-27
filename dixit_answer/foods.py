"""Finding foods in text and looking them up (phase 2).

Two indexes:
- the food master (curated, with allergens and preferred records): used to recognise foods in a message and in answers;
- the library name index: whole-word search over all loaded records, for foods the food master doesn't cover yet.
No fixed India-first or USA-first order (N3); ties between different dishes are never broken silently (N14).
"""
from __future__ import annotations

from dataclasses import dataclass

from .loader import FLAG_ENERGY_MISSING, FLAG_MACRO_MISMATCH, FLAG_MACROS_INCOMPLETE, FLAG_SERVING_UNCLEAR, Library
from .textnorm import clean, has_phrase


@dataclass(frozen=True)
class FoodMention:
    food_id: str
    alias: str
    position: int


class FoodIndex:
    def __init__(self, food_master: dict, spelling_map: dict[str, str] | None = None):
        self.spelling_map = spelling_map or {}
        self.foods: dict[str, dict] = {f["id"]: f for f in food_master["foods"]}
        # Foods added only so book recipes can be calculated (match_in_chat false) are never read from a message.
        self.book_only: set[str] = {f["id"] for f in food_master["foods"] if f.get("match_in_chat") is False}
        self.alias_to_id: dict[str, str] = {}
        for food in food_master["foods"]:
            if food["id"] in self.book_only:
                continue
            for alias in [food["name"], *food.get("aliases", [])]:
                self.alias_to_id.setdefault(clean(alias, self.spelling_map), food["id"])
        # Longest aliases first, so "sweet potato" wins over "potato" and "moong dal" over "dal".
        self._aliases = sorted(self.alias_to_id, key=lambda a: (-len(a.split()), -len(a)))

    def name(self, food_id: str) -> str:
        return self.foods[food_id]["name"]

    def with_generics(self, food_ids) -> set[str]:
        """The foods plus their generic parents (moong dal also counts as dal when steps say "the dal")."""
        out = set(food_ids)
        for fid in food_ids:
            parent = self.foods.get(fid, {}).get("generic")
            if parent:
                out.add(parent)
        return out

    def find(self, text: str, already_clean: bool = False) -> list[FoodMention]:
        cleaned = text if already_clean else clean(text, self.spelling_map)
        taken: list[tuple[int, int]] = []
        found: list[FoodMention] = []
        for alias in self._aliases:
            start = 0
            while True:
                idx = cleaned.find(alias, start)
                if idx < 0:
                    break
                end = idx + len(alias)
                before_ok = idx == 0 or not cleaned[idx - 1].isalnum()
                after_ok = end == len(cleaned) or not cleaned[end].isalnum()
                overlaps = any(not (end <= a or idx >= b) for a, b in taken)
                if before_ok and after_ok and not overlaps:
                    taken.append((idx, end))
                    found.append(FoodMention(self.alias_to_id[alias], alias, idx))
                start = end
        found.sort(key=lambda m: m.position)
        unique: list[FoodMention] = []
        seen: set[str] = set()
        for mention in found:
            if mention.food_id not in seen:
                seen.add(mention.food_id)
                unique.append(mention)
        return unique

    def mentions_any(self, cleaned_text: str, food_ids) -> bool:
        return any(has_phrase(cleaned_text, clean(a, self.spelling_map))
                   for fid in food_ids for a in [self.foods[fid]["name"], *self.foods[fid].get("aliases", [])])


class LibrarySearch:
    """Whole-word search over the loaded library records."""

    def __init__(self, library: Library, bad_mappings: list[tuple[str, str]] | None = None):
        self.library = library
        self.bad_mappings = bad_mappings or []    # (query word, record-name word): never return that record (black list)
        self._names: list[tuple[str, set[str], str]] = []   # (record_id, words, cleaned name)
        self._by_word: dict[str, list[int]] = {}             # word -> positions in _names, so a search only looks at names that hold its rarest word
        for rid, rec in library.records.items():
            names = {clean(rec.get("food_name", ""))}
            names.update(clean(a) for a in rec.get("aliases") or [])
            for name in names:
                if name:
                    words = set(name.split())
                    self._names.append((rid, words, name))
                    for word in words:
                        self._by_word.setdefault(word, []).append(len(self._names) - 1)

    def search(self, query: str, limit: int = 5, usda_only: bool = False) -> list[dict]:
        q = clean(query)
        q_words = set(q.split())
        if not q_words:
            return []
        best: dict[str, tuple] = {}
        rarest = min((self._by_word.get(w, []) for w in q_words), key=len)
        for position in rarest:
            rid, words, name = self._names[position]
            if not q_words <= words or (usda_only and self.library.is_indian(rid)):
                continue
            if any(qw in q_words and nw in words for qw, nw in self.bad_mappings):
                continue
            if FLAG_MACRO_MISMATCH in self.library.flags.get(rid, set()):
                continue
            rec = self.library.records[rid]
            exact = name == q
            extra = len(words - q_words)
            prepared = rec.get("record_type") == "prepared_food"
            quality = rec.get("data_quality") or {}
            published = quality.get("energy_basis") == "source_published"
            # Identity first (the four above), then record quality. Quality only orders records of the same dish;
            # ties between different dishes still go to resolve() as ambiguous (N14).
            flags = self.library.flags.get(rid, set())
            complete = not (flags & {FLAG_ENERGY_MISSING, FLAG_MACROS_INCOMPLETE, FLAG_SERVING_UNCLEAR})
            confidence = quality.get("calorie_confidence_pct")
            confidence = float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) \
                and 0 <= confidence <= 100 else 0.0
            score = (exact, -extra, prepared, published, complete, confidence)
            if rid not in best or score > best[rid][0]:
                best[rid] = (score, name)
        ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
        return [{"record_id": rid, "food_name": self.library.records[rid]["food_name"], "score": s, "prepared": s[2]} for rid, (s, _) in ranked]

    def resolve(self, query: str) -> dict:
        """Best record for a query, or an ambiguity (two different dishes scoring the same) to ask about."""
        hits = self.search(query, limit=5)
        if not hits:
            return {"status": "not_found", "query": query}
        if len(hits) > 1 and hits[0]["score"][:4] == hits[1]["score"][:4] and \
                clean(hits[0]["food_name"]) != clean(hits[1]["food_name"]):
            return {"status": "ambiguous", "query": query, "candidates": hits[:2]}
        return {"status": "found", "query": query, "record_id": hits[0]["record_id"], "food_name": hits[0]["food_name"]}

"""Where the numbers in an answer come from, by name, shown beside the answer (plugin step 8).

Every nutrition value names its source dataset, as the library record states it ("USDA FoodData Central (Foundation)", "INDB-derived
Swastham Indian Meal Nutrient Library"), with the record IDs behind it. A source is **trusted** only when production may load it
(`manifest.json`, environments.production.sources): today that is USDA alone. The Indian sources are development data until their
licence is confirmed, and the answer says so instead of hiding it.
"""
from __future__ import annotations

USDA, INDIAN, INDB_CALC, BOOK = "usda", "indian", "indb_calc", "book"
NOTES = {
    USDA: "Official U.S. Department of Agriculture data, public domain.",
    INDIAN: "Derived from the Indian Nutrient Databank (INDB). Licence not yet confirmed, so it is used in development only.",
    INDB_CALC: "An INDB recipe; its values were calculated by us from the ingredients. Licence not yet confirmed, so it is used in "
               "development only.",
    BOOK: "The Dixit programme's recipe book (ADORE Trust, Pune, 2021): the dish, ingredients and steps. Its printed calories are not "
          "used; the numbers come from USDA.",
}
BOOK_UNSIGNED = " Transcribed for this app; our dietitian has not yet checked the transcription."
SHORT = {USDA: "USDA FoodData Central", INDIAN: "INDB (Indian library)", INDB_CALC: "INDB recipe, calculated", BOOK: "Healthful Diet book"}
MAX_RECORDS = 12


def answer_sources(draft, library, index, dishes, production_sources) -> list[dict]:
    """[{"key", "name", "short", "trusted", "note", "used_for", "records": [{"id", "food"}]}], trusted sources first."""
    groups: dict[str, dict] = {}

    def add(key: str, name: str, used_for: str, record_id: str, food: str, trusted: bool | None = None, note: str = "") -> None:
        if trusted is None:
            trusted = (INDIAN if key == INDB_CALC else key) in production_sources
        group = groups.setdefault(name, {"key": key, "name": name, "short": SHORT[key], "trusted": trusted,
                                         "note": NOTES[key] + note, "used_for": used_for, "records": []})
        if used_for not in group["used_for"]:
            group["used_for"] += f", {used_for}"
        entry = {"id": record_id, "food": food}
        if entry not in group["records"] and len(group["records"]) < MAX_RECORDS:
            group["records"].append(entry)

    def add_record(rid: str, used_for: str) -> None:
        rec = library.records.get(rid)
        if rec is None:
            return
        key = INDIAN if library.is_indian(rid) else USDA
        name = rec.get("source_dataset") or SHORT[key]
        if key == USDA and "FoodData Central" not in name:          # "USDA SR Legacy" is one of FoodData Central's datasets: say so
            name = f"USDA FoodData Central · {name.removeprefix('USDA ').strip()}"
        add(key, name, used_for, str(rec.get("source_record_id") or rid), rec.get("food_name", ""))

    for fact in draft.facts:
        rid = fact.record_id
        if rid.startswith("DISH-"):
            code = rid[len("DISH-"):]
            library_rid = dishes.by_code.get(code) if dishes is not None else None
            if library_rid:
                add_record(library_rid, "nutrition values")
            else:
                add(INDB_CALC, "INDB recipes (Indian Nutrient Databank)", "nutrition values", f"INDB {code}", fact.food_name)
        else:
            add_record(rid, "nutrition values")
    if draft.recipe is not None:
        book = getattr(draft.recipe, "book", None)
        if book:
            signed = bool(getattr(draft.recipe, "source_signed", False))
            page = f", page {book['page']}" if book.get("page") else ""
            add(BOOK, "Healthful Diet: Think wise. Eat twice. Drop size (ADORE Trust, 2021)", "the recipe",
                f"{book['id']}{page}", draft.recipe.name, trusted=signed, note="" if signed else BOOK_UNSIGNED)
        for item in draft.recipe.ingredients:
            food = index.foods.get(item["food"]) or {}
            for fid in [p["food"] for p in food.get("composition") or []] or [item["food"]]:      # a mixed flour: its grains
                rid = (index.foods.get(fid) or {}).get("ingredient_record")
                if rid:
                    add_record(rid, "recipe calculation")
    return sorted(groups.values(), key=lambda g: (not g["trusted"], g["name"]))

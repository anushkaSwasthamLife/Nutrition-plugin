"""Doctor packs (plugin step 2): the doctor's layer of the constraints, per programme (`data/doctor_packs.json`).

A pack can only add restrictions: foods the programme never uses and lower nutrient limits. It also names the recipes the doctor
recommends, which the recipe search tries first and shows with the doctor's badge. The layers merge strictest first (step 4):
doctor pack, then the patient's safety (allergies, conditions, medicines), then preferences (diet, avoid list). A pack never
relaxes a layer below it, because blocks are a union and limits a minimum.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .programme import NEUTRAL, Programme, build as build_programme

TEST_STATUSES = {"TEST ONLY", "STUB", "PARTIAL", "PROPOSED", "DRAFT"}


@dataclass(frozen=True)
class DoctorPack:
    doctor_id: str
    doctor_name: str
    programme: str
    label: str
    never_foods: frozenset = frozenset()
    limits: tuple = ()                               # ({"condition", "nutrient", "daily_max"}, ...)
    approved_recipes: frozenset = frozenset()
    recipe_books: frozenset = frozenset()            # the programme's recipe books, searched right after the doctor's own list
    signed: bool = False                             # the doctor signed the pack and the file is not test data
    notes: tuple = field(default=())                 # why the pack differs from what was asked for (audit)
    programme_obj: Programme = NEUTRAL               # identity, own lines and rules of the programme (programme.py)

    def as_audit(self) -> dict:
        return {"doctor_id": self.doctor_id, "programme": self.programme, "signed": self.signed,
                "rules": self.programme_obj.rules.version, "rule_types": sorted(self.programme_obj.rules.types),
                "never_foods": len(self.never_foods), "limits": len(self.limits), "approved_recipes": len(self.approved_recipes),
                "notes": list(self.notes)}


class DoctorPacks:
    def __init__(self, data: dict, programme_rules: dict | None = None):
        self.version = data["version"]
        self.default_id = data["default_doctor_id"]
        self.packs = data["packs"]
        self.test_file = str(data.get("status", "")).upper() in TEST_STATUSES
        self.programme_rules = programme_rules or {}

    def resolve(self, doctor_id: str | None = None, programme: str | None = None) -> DoctorPack:
        """The pack for this doctor and programme. An unknown doctor or programme gets the default, and the audit says so."""
        notes = []
        if doctor_id and doctor_id not in self.packs:
            notes.append(f"unknown doctor '{str(doctor_id)[:40]}': default pack used")
        did = doctor_id if doctor_id in self.packs else self.default_id
        pack = self.packs[did]
        if programme and programme not in pack["programmes"]:
            notes.append(f"unknown programme '{str(programme)[:40]}': the doctor's default programme used")
        pid = programme if programme in pack["programmes"] else pack["default_programme"]
        prog = pack["programmes"][pid]
        signed = bool(pack.get("approved_by")) and bool(pack.get("approved_on")) and not self.test_file
        return DoctorPack(doctor_id=did, doctor_name=pack["doctor_name"], programme=pid, label=prog["label"],
                          never_foods=frozenset(prog.get("never_foods") or []), limits=tuple(prog.get("limits") or []),
                          approved_recipes=frozenset(prog.get("approved_recipes") or []),
                          recipe_books=frozenset(prog.get("recipe_books") or []), signed=signed, notes=tuple(notes),
                          programme_obj=build_programme(pid, prog, self.programme_rules))

"""The two real chats from the analysis, joined (architecture page section 12), run end to end with the template wording."""

FULL_SEQUENCE = "nuts → salad → sprouts → your usual home-cooked meal"


def failed(result):
    return [c for c in result.checks if not c["passed"]]


def test_six_turn_conversation(chat):
    r = chat(
        "i have obesity . so what should i eat to reduce it",
        "No medicines. I have chapati, what sabji can I eat?",
        "bhindi, aloo, cauliflower",
        "can i eat aloo",
        "give me recipes",
        "I have aloo and onion, what Indian meal can I prepare?",
        "Aloo Pyaz ki Sabji",
    )
    for result in r:
        assert not failed(result), (result.turn, failed(result))

    # Turn 1 (changed 22 Oct): the question is answered with food. No warm line, no caution and no full programme text in front
    # (those come with a question about meal timing); named foods; then one question, the allergies.
    t1 = r[0].answer
    assert not t1.startswith("Thank you for asking") and "quick check" not in t1 and FULL_SEQUENCE not in t1
    assert "dal" in t1 and "Have less often" in t1
    assert r[0].plan["followup"]["id"] == "FU-ALLERGY"

    # The full sequence is never shown unasked.
    assert all(FULL_SEQUENCE not in x.answer for x in r)

    # Turn 2: the medicines are understood without having been asked; named sabjis; a sourced number; asks what's cooked at home.
    t2 = r[1].answer
    assert "bhindi" in t2 and "aloo" in t2 and "calories" in t2
    assert r[1].plan["followup"]["slot"] == "home_foods"

    # Turn 3: the short reply is read as an answer and the three sabjis are compared.
    assert r[2].plan["reply_slot"] == "home_foods"
    for food in ("Bhindi", "Aloo", "Cauliflower"):
        assert f"**{food}**" in r[2].answer
    assert "starchy" in r[2].answer

    # Turn 4: aloo is allowed, starchy, with a number that says which dish it is for.
    assert r[3].answer.startswith("Aloo is allowed in the programme")
    assert "starchy" in r[3].answer and "dry potato" in r[3].answer

    # Turn 5: "give me recipes" offers approved dishes and asks which; turn 6 lists the dishes that fit aloo and onion; turn 7 builds the recipe.
    assert "light dishes" in r[4].answer and "In your meal" not in r[4].answer and "Which one should I show?" in r[4].answer
    assert "Aloo Pyaz ki Sabji" in r[5].answer and "Which one should I show?" in r[5].answer      # aloo and onion fit several approved dishes: they are listed
    t7 = r[6].answer
    assert "Aloo Pyaz ki Sabji" in t7 and "**You need**" in t7 and "**How:**" in t7
    assert "372 kcal" in t7 and "calculated from USDA" in t7             # whole-dish numbers worked out by code
    assert "In your meal" in t7 and "Thanks, noted" not in t7


def test_no_status_question_for_a_plain_fact(chat):
    (r,) = chat("How many carbs in one chapati?")
    assert "100 g of chapati" in r.answer and "46.1 g of carbohydrate" in r.answer
    assert "per-piece" in r.answer                      # honest: no unit weight yet
    assert not (r.plan["followup"] and r.plan["followup"]["slot"] == "safety")


def test_timing_question_gets_caution_and_no_snacks(chat):
    (r,) = chat("Can I snack between meals?")
    assert "quick check" in r.answer and "Nothing in between meals" in r.answer
    assert not failed(r)


def test_emergency_skips_everything_else(chat):
    (r,) = chat("I took insulin and now I'm shaking and sweating")
    assert r.type == "emergency" and "112" in r.answer
    assert "nuts" not in r.answer


def test_known_risk_gets_doctor_first(chat):
    r = chat("I take insulin. How does the program work?")
    assert "Because you take medicine for diabetes" in r[0].answer
    assert "not something to start now" in r[0].answer


def test_out_of_scope(chat):
    (r,) = chat("Which phone should I buy?")
    assert r.answer.startswith("I can only help with food")


def test_vegetarian_preference_is_remembered(chat, engine):
    r = chat("what should i eat", "no", "I'm vegetarian")
    state = engine.store.load(r[0].conversation_id)
    assert state.food_preference == "vegetarian"

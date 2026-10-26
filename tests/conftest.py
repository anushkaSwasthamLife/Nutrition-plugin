import copy

import pytest

import dixit_answer.pipeline as pipeline_module

from dixit_answer.config import load_settings
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine


# The first eight approved recipes (RCP-0.1.0). Most tests were written against them; the larger collection (RCP-0.2.0) is tested
# on its own through the `full_engine` fixture.
CLASSIC_RECIPES = {"RCP-SABJI-001", "RCP-SABJI-002", "RCP-SABJI-003", "RCP-SABJI-004", "RCP-SABJI-005", "RCP-SABJI-006", "RCP-SABJI-007",
                   "RCP-DAL-001"}
_real_load = pipeline_module.load_swastham_data


def _classic_load(settings, manifest=None):
    data = _real_load(settings, manifest)
    data.recipes["recipes"] = [r for r in data.recipes["recipes"] if r["recipe_id"] in CLASSIC_RECIPES]
    return data


@pytest.fixture(scope="session", autouse=True)
def _classic_recipe_collection():
    pipeline_module.load_swastham_data = _classic_load
    yield
    pipeline_module.load_swastham_data = _real_load


@pytest.fixture(scope="session")
def full_engine(settings, _classic_recipe_collection):
    """An engine with the whole recipe collection (RCP-0.2.0), as the chat page runs it."""
    pipeline_module.load_swastham_data = _real_load
    try:
        return Engine(copy.copy(settings), llm=MockLLM())
    finally:
        pipeline_module.load_swastham_data = _classic_load


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    # The INDB dish lookup is tested on its own (test_dish_chat.py); the rest of the suite runs without it.
    # The Healthful Diet book is tested on its own (test_book_recipes.py); the rest of the suite runs without it.
    return load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("var"), dish_lookup=False,
                         book_recipes_file=None)


@pytest.fixture(scope="session")
def engine(settings):
    return Engine(settings, llm=MockLLM())


@pytest.fixture
def chat(engine):
    """Send messages in one fresh conversation; returns the list of results."""
    def run(*messages):
        results, cid = [], None
        for message in messages:
            result = engine.answer(message, cid)
            cid = result.conversation_id
            results.append(result)
        return results
    return run

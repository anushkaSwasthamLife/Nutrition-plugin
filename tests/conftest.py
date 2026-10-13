import pytest

from dixit_answer.config import load_settings
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    return load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("var"))


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

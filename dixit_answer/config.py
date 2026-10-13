"""Settings for the Dixit answer pipeline.

Read from environment variables, then from `.env` in the project folder. The language-model settings (LLM_*) can also come
from another `.env` named by LLM_ENV_FILE, so the Swastham Report analysis key is reused without copying it (user decision
13 Oct 2026: "same AI as Report analysis" = Groq, openai/gpt-oss-120b).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
DATA_DIR = PACKAGE_DIR / "data"

DEFAULT_LIBRARY_DIR = Path.home() / "Desktop" / "Nutrition" / "Swastham_Nutrition_Data_Library_v1_0_3"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path or not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class Settings:
    environment: str = "development"            # development | production
    library_dir: Path = DEFAULT_LIBRARY_DIR
    var_dir: Path = PROJECT_DIR / "var"          # chat state and audit log (gitignored)
    llm_provider: str = "mock"                   # mock | groq
    llm_api_key: str = field(default="", repr=False)
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: int = 30
    llm_reasoning: str = "low"
    port: int = 8100                             # not 8000 / 5173: those are the Swastham website servers

    @property
    def state_db(self) -> Path:
        return self.var_dir / "chat_state.sqlite3"

    @property
    def audit_log(self) -> Path:
        return self.var_dir / "audit.jsonl"


def load_settings(**overrides) -> Settings:
    env = _read_env_file(PROJECT_DIR / ".env")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("DIXIT_", "LLM_"))})

    llm_env_file = env.get("LLM_ENV_FILE", "")
    if llm_env_file:
        shared = _read_env_file(Path(llm_env_file))
        for key in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY", "GROQ_API_KEY"):
            if key in shared and key not in env:
                env[key] = shared[key]

    api_key = env.get("LLM_API_KEY") or env.get("GROQ_API_KEY") or ""
    provider = (env.get("LLM_PROVIDER") or ("groq" if api_key else "mock")).lower()
    if provider == "groq" and not api_key:
        provider = "mock"   # no key on this machine: tests and the chat page still work

    settings = Settings(
        environment=env.get("DIXIT_ENVIRONMENT", "development").lower(),
        library_dir=Path(env.get("DIXIT_LIBRARY_DIR") or DEFAULT_LIBRARY_DIR),
        var_dir=Path(env.get("DIXIT_VAR_DIR") or PROJECT_DIR / "var"),
        llm_provider=provider,
        llm_api_key=api_key,
        llm_base_url=env.get("LLM_BASE_URL") or "https://api.groq.com/openai/v1",
        llm_model=env.get("LLM_MODEL") or "openai/gpt-oss-120b",
        llm_timeout_seconds=int(env.get("LLM_TIMEOUT_SECONDS") or 30),
        llm_reasoning=env.get("LLM_REASONING", "low"),
        port=int(env.get("DIXIT_PORT") or 8100),
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings

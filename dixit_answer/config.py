"""Settings for the Dixit answer pipeline.

Read from environment variables, then from `.env` in the project folder. The language-model settings (LLM_*) can also come
from another `.env` named by LLM_ENV_FILE, so the Swastham Report analysis key is reused without copying it (user decision
13 Oct 2026: "same AI as Report analysis" = Groq, openai/gpt-oss-120b).
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
DATA_DIR = PACKAGE_DIR / "data"

LIBRARY_NAME = "Swastham_Nutrition_Data_Library_v1_0_5"
# The Nutrition folder sits beside this project on the Desktop. Follow the project's own location, because the Desktop can
# live under OneDrive (it moved there on 19 Oct 2026) and then Path.home() / "Desktop" is an empty stub.
_BESIDE_PROJECT = PROJECT_DIR.parent / "Nutrition" / LIBRARY_NAME
DEFAULT_LIBRARY_DIR = _BESIDE_PROJECT if _BESIDE_PROJECT.is_dir() else Path.home() / "Desktop" / "Nutrition" / LIBRARY_NAME


# The Dixit programme's recipe book (Healthful Diet), transcribed to JSON. Private circulation: it is never copied into the repo.
DEFAULT_BOOK_RECIPES = PROJECT_DIR.parent / "Nutrition" / "healthful-diet" / "healthful-diet-recipes.json"
LOOPBACK_HOST = "127.0.0.1"
_HOST_NAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


def parse_host(value: str) -> str:
    """The address the web server listens on: an IP address or a host name. Empty means this machine only (127.0.0.1)."""
    value = (value or "").strip()
    if not value:
        return LOOPBACK_HOST
    try:
        return str(ipaddress.ip_address(value.strip("[]")))
    except ValueError:
        pass
    if _HOST_NAME.match(value) and not re.fullmatch(r"[\d.]+", value):        # "1.2.3.4.5" is a mistyped address, not a name
        return value.lower()
    raise ValueError(f"DIXIT_HOST must be an IP address or a host name, for example 127.0.0.1 or 0.0.0.0 (got {value!r}).")


def is_loopback(host: str) -> bool:
    """True for an address that only this machine can reach (127.x.x.x, ::1, localhost)."""
    host = (host or "").strip().strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
    nutrition_dir: Path = PROJECT_DIR.parent / "Nutrition"   # INDB source files and releases (development only)
    dish_lookup: bool = True                     # answer "calories in poha" from INDB dishes (development only; off in production)
    book_recipes_file: Path | None = DEFAULT_BOOK_RECIPES   # the Healthful Diet recipes file (kept outside the repo); None = off
    llm_provider: str = "mock"                   # mock | groq
    llm_api_key: str = field(default="", repr=False)
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: int = 30
    llm_reasoning: str = "low"
    memory: bool = True                          # remember facts and keep past conversations per person (development only; off in production)
    port: int = 8100                             # not 8000 / 5173: those are the Swastham website servers
    host: str = LOOPBACK_HOST                    # 127.0.0.1 = this machine only; 0.0.0.0 = reachable from the network (DIXIT_HOST)

    @property
    def state_db(self) -> Path:
        return self.var_dir / "chat_state.sqlite3"

    @property
    def memory_db(self) -> Path:
        return self.var_dir / "memory.sqlite3"

    @property
    def audit_log(self) -> Path:
        return self.var_dir / "audit.jsonl"


def _book_path(value: str) -> Path | None:
    value = value.strip()
    if value.lower() in {"off", "0", "false", "no", "none"}:
        return None
    return Path(value) if value else DEFAULT_BOOK_RECIPES


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
        nutrition_dir=Path(env.get("DIXIT_NUTRITION_DIR") or PROJECT_DIR.parent / "Nutrition"),
        dish_lookup=env.get("DIXIT_DISH_LOOKUP", "on").strip().lower() not in {"off", "0", "false", "no"},
        book_recipes_file=_book_path(env.get("DIXIT_BOOK_RECIPES", "")),
        llm_provider=provider,
        llm_api_key=api_key,
        llm_base_url=env.get("LLM_BASE_URL") or "https://api.groq.com/openai/v1",
        llm_model=env.get("LLM_MODEL") or "openai/gpt-oss-120b",
        llm_timeout_seconds=int(env.get("LLM_TIMEOUT_SECONDS") or 30),
        llm_reasoning=env.get("LLM_REASONING", "low"),
        memory=env.get("DIXIT_MEMORY", "on").strip().lower() not in {"off", "0", "false", "no"},
        port=int(env.get("DIXIT_PORT") or 8100),
        host=parse_host(env.get("DIXIT_HOST", "")),
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings

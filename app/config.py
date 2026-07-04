from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?}")


def load_local_env() -> None:
    for filename in (".env.local", ".env"):
        path = ROOT / filename
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _expand(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.groups()
        return os.getenv(name, default or "")

    return ENV_PATTERN.sub(replace, value)


class OpenAIConfig(BaseModel):
    model: str
    max_turns: int = 12
    tracing_enabled: bool = True


class MCPToolsConfig(BaseModel):
    allowlist: list[str]
    blocklist: list[str]
    approval_required: list[str]


class MCPConfig(BaseModel):
    server_name: str
    url: str
    stateless_http: bool = True
    json_response: bool = True
    timeout_seconds: int = 20
    tools: MCPToolsConfig


class AgentDefinition(BaseModel):
    name: str
    instructions: str


class SecurityConfig(BaseModel):
    default_role: str = "employee"
    permitted_roles: list[str]
    rate_limit_per_minute: int = 20
    pii_masking: bool = True
    audit_retention: int = 5000


class Settings(BaseModel):
    application: dict[str, Any]
    openai: OpenAIConfig
    mcp: MCPConfig
    agents: dict[str, AgentDefinition]
    security: SecurityConfig
    root: Path = Field(default=ROOT, exclude=True)

    @property
    def has_usable_api_key(self) -> bool:
        key = os.getenv("OPENAI_API_KEY", "")
        return key.startswith("sk-") and len(key) > 30


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_local_env()
    raw = yaml.safe_load((ROOT / "config" / "agents.yml").read_text(encoding="utf-8"))
    return Settings.model_validate(_expand(raw))


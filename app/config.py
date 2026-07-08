"""YAMLと環境変数をPythonから安全に使える設定へ変換するモジュール。

用途:
    ``config/agents.yml`` を読み、Pydanticで型と必須項目を検証してSettingsを返す。
必要な理由:
    Agent名、MCP URL、Tool権限をコードへ散在させず、環境ごとに安全に変更するため。
関連ファイル:
    ``main.py``、``app/agent_service.py``、両MCPサーバーが同じSettingsを共有する。
    ローカルの秘密値はGit管理外の ``.env.local`` から読み、項目例は ``.env.example`` に置く。
"""

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
    """ローカル開発用envファイルを既存環境変数を上書きせず読み込む。"""
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
    """YAML内の${NAME:-default}表記を再帰的に環境変数展開する。"""
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


class AuthConfig(BaseModel):
    session_secret: str
    mcp_token_secret: str
    session_minutes: int = 480
    mcp_token_minutes: int = 5
    cookie_secure: bool = False
    cookie_name: str = "agent_session"


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


class MCPRegistryConfig(BaseModel):
    servers: dict[str, MCPConfig]


class AgentDefinition(BaseModel):
    name: str
    instructions: str


class SecurityConfig(BaseModel):
    default_role: str = "employee"
    permitted_roles: list[str]
    rate_limit_per_minute: int = 20
    pii_masking: bool = True
    audit_retention: int = 5000
    approval_roles: list[str]


class Settings(BaseModel):
    application: dict[str, Any]
    auth: AuthConfig
    openai: OpenAIConfig
    mcp: MCPRegistryConfig
    agents: dict[str, AgentDefinition]
    security: SecurityConfig
    root: Path = Field(default=ROOT, exclude=True)

    @property
    def has_usable_api_key(self) -> bool:
        key = os.getenv("OPENAI_API_KEY", "")
        return key.startswith("sk-") and len(key) > 30


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """検証済み設定をプロセス内で1回だけ生成し再利用する。"""
    load_local_env()
    raw = yaml.safe_load((ROOT / "config" / "agents.yml").read_text(encoding="utf-8"))
    settings = Settings.model_validate(_expand(raw))
    if settings.application["environment"] == "production":
        secrets_to_check = (settings.auth.session_secret, settings.auth.mcp_token_secret)
        if any(secret.startswith("development-") or len(secret) < 32 for secret in secrets_to_check):
            raise ValueError("production requires independent authentication secrets of at least 32 characters")
    return settings

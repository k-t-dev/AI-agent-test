"""利用者とMCP呼び出し元を確認する認証モジュール。

用途:
    パスワード検証、ブラウザ用署名セッション、CSRF値、短寿命MCPトークンを管理する。
必要な理由:
    画面入力に書かれたroleや部署を信用せず、サーバーが確認した本人情報だけで認可するため。
関連ファイル:
    利用者情報は ``config/users.yml``、秘密値と有効時間は ``config/agents.yml`` から読む。
    ``main.py`` がUIセッションに使い、``app/agent_service.py`` がMCPトークン発行に使う。
    各MCPでは ``app/mcp_identity.py`` を通して同じトークンを再検証する。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import yaml
from pydantic import BaseModel

from app.config import Settings


class Principal(BaseModel):
    """認証後にのみ生成される、RBAC/ABAC判断用の信頼済みユーザー属性。"""
    id: str
    email: str
    display_name: str
    tenant_id: str
    role: str
    department: str
    clearance: int
    scopes: list[str]


class AuthManager:
    """パスワード検証とUI・MCP向けJWTの発行・検証を担当する。"""
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        raw = yaml.safe_load((settings.root / "config" / "users.yml").read_text(encoding="utf-8"))
        self.users = {user["email"].lower(): user for user in raw["users"]}

    @staticmethod
    def _verify_password(password: str, encoded: str) -> bool:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)

    def authenticate(self, email: str, password: str) -> Principal | None:
        """メールを正規化し、PBKDF2ハッシュを定数時間比較してPrincipalを返す。"""
        user = self.users.get(email.strip().lower())
        if not user or not self._verify_password(password, user["password_hash"]):
            return None
        return Principal.model_validate({key: value for key, value in user.items() if key != "password_hash"})

    def issue_session(self, principal: Principal) -> tuple[str, str]:
        """UI用の期限付き署名セッションと対応するCSRF値を発行する。"""
        now = datetime.now(UTC)
        csrf = secrets.token_urlsafe(24)
        payload = {
            **principal.model_dump(),
            "csrf": csrf,
            "aud": "agent-ui",
            "iat": now,
            "exp": now + timedelta(minutes=self.settings.auth.session_minutes),
        }
        token = jwt.encode(payload, self.settings.auth.session_secret, algorithm="HS256")
        return token, csrf

    def read_session(self, token: str) -> tuple[Principal, str]:
        """署名、audience、期限を検証してUIセッションを復元する。"""
        payload = jwt.decode(token, self.settings.auth.session_secret, algorithms=["HS256"], audience="agent-ui")
        return Principal.model_validate(payload), str(payload["csrf"])

    def issue_mcp_token(self, principal: dict[str, Any], run_id: str | None = None) -> str:
        """AgentからMCPへ最小限の認可属性を渡す短寿命トークンを発行する。"""
        now = datetime.now(UTC)
        claims = {
            "sub": principal["id"],
            "tenant_id": principal["tenant_id"],
            "role": principal["role"],
            "department": principal["department"],
            "clearance": principal["clearance"],
            "scopes": principal["scopes"],
            "run_id": run_id,
            "aud": "enterprise-mcp",
            "iat": now,
            "exp": now + timedelta(minutes=self.settings.auth.mcp_token_minutes),
        }
        return jwt.encode(claims, self.settings.auth.mcp_token_secret, algorithm="HS256")

    def verify_mcp_token(self, token: str) -> dict[str, Any]:
        """MCP側で署名、audience、期限を再検証する。"""
        return jwt.decode(token, self.settings.auth.mcp_token_secret, algorithms=["HS256"], audience="enterprise-mcp")

    @staticmethod
    def require_scope(claims: dict[str, Any], scope: str) -> None:
        """必要scopeがなければfail-closedで拒否する。"""
        if scope not in claims.get("scopes", []):
            raise PermissionError(f"required scope is missing: {scope}")

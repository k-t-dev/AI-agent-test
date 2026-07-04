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
    id: str
    email: str
    display_name: str
    tenant_id: str
    role: str
    department: str
    clearance: int
    scopes: list[str]


class AuthManager:
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
        user = self.users.get(email.strip().lower())
        if not user or not self._verify_password(password, user["password_hash"]):
            return None
        return Principal.model_validate({key: value for key, value in user.items() if key != "password_hash"})

    def issue_session(self, principal: Principal) -> tuple[str, str]:
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
        payload = jwt.decode(token, self.settings.auth.session_secret, algorithms=["HS256"], audience="agent-ui")
        return Principal.model_validate(payload), str(payload["csrf"])

    def issue_mcp_token(self, principal: dict[str, Any], run_id: str | None = None) -> str:
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
        return jwt.decode(token, self.settings.auth.mcp_token_secret, algorithms=["HS256"], audience="enterprise-mcp")

    @staticmethod
    def require_scope(claims: dict[str, Any], scope: str) -> None:
        if scope not in claims.get("scopes", []):
            raise PermissionError(f"required scope is missing: {scope}")

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context

from app.auth import AuthManager


def authorized_claims(ctx: Context, auth: AuthManager, required_scope: str) -> dict[str, Any]:
    meta = ctx.request_context.meta
    if meta is None:
        raise PermissionError("MCP authentication metadata is required")
    if isinstance(meta, dict):
        values = meta
    elif hasattr(meta, "model_dump"):
        values = meta.model_dump(by_alias=True)
    else:
        values = vars(meta)
    token = values.get("auth_token") or values.get("authToken")
    if not token:
        raise PermissionError("MCP authentication token is required")
    claims = auth.verify_mcp_token(str(token))
    auth.require_scope(claims, required_scope)
    return claims


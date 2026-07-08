"""MCP Toolの直前で利用者情報を再検証する共通処理。

用途:
    MCPリクエストの ``_meta`` から署名付きトークンを取り出し、claimsへ変換してscopeを確認する。
必要な理由:
    API側の認証だけに依存せず、データへ最も近いMCP側でも不正アクセスを止めるため。
関連ファイル:
    ``app/agent_service.py`` がトークンを ``_meta`` へ付け、``app/auth.py`` が署名を検証する。
    ``mcp_knowledge_server.py`` と ``mcp_ticket_server.py`` がTool実行前にこの関数を呼ぶ。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context

from app.auth import AuthManager


def authorized_claims(ctx: Context, auth: AuthManager, required_scope: str) -> dict[str, Any]:
    """MCP _metaからトークンを取り出し、署名・期限・scopeをfail-closedで検証する。"""
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

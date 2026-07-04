from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import uuid4

from mcp.server.fastmcp import Context, FastMCP

from app.auth import AuthManager
from app.config import get_settings
from app.mcp_identity import authorized_claims
from app.security import mask_pii
from app.storage import JsonStore

settings = get_settings()
server = settings.mcp.servers["ticketing"]
store = JsonStore(settings.root / "data", settings.security.audit_retention)
auth = AuthManager(settings)
mcp = FastMCP(
    server.server_name,
    stateless_http=server.stateless_http,
    json_response=server.json_response,
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8791")),
)


@mcp.tool()
def draft_ticket(title: str, description: str, ctx: Context) -> str:
    """承認後に経費申請チケットの下書きを作る更新Tool。呼び出し側で必ず人間承認を要求する。"""
    claims = authorized_claims(ctx, auth, "ticket:draft")
    ticket = {
        "id": f"EXP-{str(uuid4())[:8].upper()}",
        "status": "draft",
        "title": mask_pii(title),
        "description": mask_pii(description),
        "approval": "verified_by_agent_gateway",
        "tenantId": claims["tenant_id"],
        "ownerId": claims["sub"],
        "department": claims["department"],
        "createdAt": datetime.now(UTC).isoformat(),
    }
    store.append("tickets.json", ticket)
    store.audit(claims.get("run_id") or "mcp", claims["sub"], "ticket_drafted", {
        "tenantId": claims["tenant_id"],
        "ticketId": ticket["id"],
    })
    return json.dumps(ticket, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

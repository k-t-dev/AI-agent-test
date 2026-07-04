from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.security import mask_pii
from app.storage import JsonStore

settings = get_settings()
store = JsonStore(settings.root / "data", settings.security.audit_retention)
mcp = FastMCP(
    settings.mcp.server_name,
    stateless_http=settings.mcp.stateless_http,
    json_response=settings.mcp.json_response,
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8790")),
)


@mcp.tool()
def search_policy(query: str) -> str:
    """社内規程を検索する読み取り専用Tool。規程ID、名称、本文、scoreをJSONで返す。"""
    safe_query = mask_pii(query)
    terms = [term for term in safe_query.replace("？", " ").replace("。", " ").split() if term]
    results = []
    for policy in store.read("policies.json"):
        score = sum(2 for keyword in policy["keywords"] if keyword in safe_query)
        score += sum(1 for term in terms if term in policy["body"])
        if score:
            results.append({**policy, "score": score})
    results.sort(key=lambda row: row["score"], reverse=True)
    return json.dumps({"results": results[:3]}, ensure_ascii=False)


@mcp.tool()
def draft_ticket(title: str, description: str) -> str:
    """承認後に経費申請チケットの下書きを作る更新Tool。呼び出し側で必ず人間承認を要求する。"""
    ticket = {
        "id": f"EXP-{str(uuid4())[:8].upper()}",
        "status": "draft",
        "title": mask_pii(title),
        "description": mask_pii(description),
        "approval": "verified_by_agent_gateway",
        "createdAt": datetime.now(UTC).isoformat(),
    }
    store.append("tickets.json", ticket)
    return json.dumps(ticket, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

from __future__ import annotations

import json
import os

from mcp.server.fastmcp import Context, FastMCP

from app.auth import AuthManager
from app.config import get_settings
from app.mcp_identity import authorized_claims
from app.security import mask_pii
from app.storage import JsonStore

settings = get_settings()
server = settings.mcp.servers["knowledge"]
store = JsonStore(settings.root / "data", settings.security.audit_retention)
auth = AuthManager(settings)
mcp = FastMCP(
    server.server_name,
    stateless_http=server.stateless_http,
    json_response=server.json_response,
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8790")),
)


@mcp.tool()
def search_policy(query: str, ctx: Context) -> str:
    """社内規程を検索する読み取り専用Tool。規程ID、名称、本文、scoreをJSONで返す。"""
    claims = authorized_claims(ctx, auth, "policy:read")
    safe_query = mask_pii(query)
    terms = [term for term in safe_query.replace("？", " ").replace("。", " ").split() if term]
    results = []
    for policy in store.read("policies.json"):
        if policy["tenant_id"] != claims["tenant_id"]:
            continue
        if policy["minimum_clearance"] > int(claims["clearance"]):
            continue
        if policy["required_scope"] not in claims["scopes"]:
            continue
        if "*" not in policy["departments"] and claims["department"] not in policy["departments"]:
            continue
        score = sum(2 for keyword in policy["keywords"] if keyword in safe_query)
        score += sum(1 for term in terms if term in policy["body"])
        if score:
            results.append({**policy, "score": score})
    results.sort(key=lambda row: row["score"], reverse=True)
    store.audit(claims.get("run_id") or "mcp", claims["sub"], "policy_searched", {
        "tenantId": claims["tenant_id"],
        "department": claims["department"],
        "resultCount": len(results[:3]),
    })
    return json.dumps({"results": results[:3]}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

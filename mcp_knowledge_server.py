"""社内規程の読み取りだけを担当するKnowledge MCPサーバー。

用途:
    ``search_policy`` Toolを公開し、利用者が閲覧可能な規程だけを検索する。
必要な理由:
    読み取り権限と更新権限を別サーバーに分け、検索Agentへ書き込み能力を渡さないため。
関連ファイル:
    ``app/agent_service.py`` のResearch Agentから接続され、``app/mcp_identity.py`` で再認証する。
    ``data/policies.json`` を検索し、``app/storage.py`` 経由で検索監査を保存する。
    接続先とTool許可は ``config/agents.yml`` のknowledge設定で管理する。
"""

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
    """社内規程を検索する。

    呼び出し元の短寿命MCPトークンを検証し、同一tenantかつ部署、clearance、scopeを満たす規程だけを
    検索対象にする読み取り専用Tool。規程ID、名称、本文、関連度scoreをJSONで返し、検索件数を監査する。
    """
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

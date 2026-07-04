from __future__ import annotations

import os

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agent_service import AgentService
from app.auth import AuthManager, Principal
from app.config import get_settings
from app.security import RateLimiter
from app.storage import JsonStore

settings = get_settings()
store = JsonStore(settings.root / "data", settings.security.audit_retention)
service = AgentService(settings, store)
auth = AuthManager(settings)
limiter = RateLimiter(settings.security.rate_limit_per_minute)
login_limiter = RateLimiter(10)
app = FastAPI(title=settings.application["name"], version="1.0.0")


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=200)


class RunRequest(BaseModel):
    input: str = Field(min_length=3, max_length=8000)


def current_session(
    agent_session: str | None = Cookie(default=None, alias=settings.auth.cookie_name),
) -> tuple[Principal, str]:
    if not agent_session:
        raise HTTPException(status_code=401, detail="ログインが必要です")
    try:
        return auth.read_session(agent_session)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="セッションが無効または期限切れです") from exc


def current_user(session: tuple[Principal, str] = Depends(current_session)) -> Principal:
    return session[0]


def csrf_user(
    session: tuple[Principal, str] = Depends(current_session),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> Principal:
    principal, expected = session
    if not csrf_header or csrf_header != expected:
        raise HTTPException(status_code=403, detail="CSRF検証に失敗しました")
    return principal


@app.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, object]:
    limiter_key = f"{request.client.host if request.client else 'unknown'}:{payload.email.lower()}"
    if not login_limiter.allow(limiter_key):
        raise HTTPException(status_code=429, detail="ログイン試行回数が上限を超えました")
    principal = auth.authenticate(payload.email, payload.password)
    if not principal:
        store.audit("auth", "Authentication", "login_failed", {"email": payload.email.lower()})
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが違います")
    token, csrf = auth.issue_session(principal)
    response.set_cookie(
        key=settings.auth.cookie_name,
        value=token,
        httponly=True,
        secure=settings.auth.cookie_secure,
        samesite="strict",
        max_age=settings.auth.session_minutes * 60,
        path="/",
    )
    store.audit("auth", principal.id, "login_succeeded", {"role": principal.role, "tenantId": principal.tenant_id})
    return {"user": principal.model_dump(), "csrfToken": csrf}


@app.get("/api/auth/me")
async def me(session: tuple[Principal, str] = Depends(current_session)) -> dict[str, object]:
    principal, csrf = session
    return {"user": principal.model_dump(), "csrfToken": csrf}


@app.post("/api/auth/logout")
async def logout(response: Response, principal: Principal = Depends(csrf_user)) -> dict[str, bool]:
    response.delete_cookie(settings.auth.cookie_name, path="/")
    store.audit("auth", principal.id, "logout", {})
    return {"ok": True}


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "openai": "configured" if settings.has_usable_api_key else "invalid_test_key",
        "mcp": {name: server.url for name, server in settings.mcp.servers.items()},
        "agents": len(settings.agents),
        "model": settings.openai.model,
    }


@app.get("/api/config")
async def public_config() -> dict[str, object]:
    servers = settings.mcp.servers
    return {
        "model": settings.openai.model,
        "mcpServers": {name: server.url for name, server in servers.items()},
        "allowedTools": [tool for server in servers.values() for tool in server.tools.allowlist],
        "blockedTools": [tool for server in servers.values() for tool in server.tools.blocklist],
        "approvalRequired": [tool for server in servers.values() for tool in server.tools.approval_required],
    }


@app.get("/api/audit")
async def audit_log(principal: Principal = Depends(current_user)) -> list[dict[str, object]]:
    if "audit:read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="監査ログの閲覧権限がありません")
    rows = store.read("audit.json")
    tenant_run_ids = {
        row["runId"]
        for row in rows
        if row["event"] == "accepted"
        and row.get("detail", {}).get("user", {}).get("tenant_id") == principal.tenant_id
    }
    visible = [
        row for row in rows
        if row["runId"] in tenant_run_ids
        or row.get("detail", {}).get("tenantId") == principal.tenant_id
    ]
    return list(reversed(visible[-100:]))


@app.get("/api/approvals")
async def pending_approvals(principal: Principal = Depends(current_user)) -> list[dict[str, object]]:
    if principal.role not in settings.security.approval_roles or "ticket:approve" not in principal.scopes:
        return []
    return [
        {
            "runId": row["runId"],
            "createdAt": row["createdAt"],
            "requestedBy": row["requestedBy"],
        }
        for row in store.read("pending_runs.json")
        if row.get("tenantId") == principal.tenant_id
    ]


@app.post("/api/runs", status_code=201)
async def create_run(payload: RunRequest, principal: Principal = Depends(csrf_user)) -> dict[str, object]:
    if principal.role not in settings.security.permitted_roles:
        raise HTTPException(status_code=403, detail="role is not permitted")
    if not limiter.allow(principal.id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    if not settings.has_usable_api_key:
        raise HTTPException(status_code=503, detail="有効なOPENAI_API_KEYが必要です。現在はテスト用ダミー値です。")
    try:
        return await service.start(payload.input, principal.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AgentまたはMCPの実行に失敗しました") from exc


@app.post("/api/runs/{run_id}/approve")
async def approve_run(run_id: str, principal: Principal = Depends(csrf_user)) -> dict[str, object]:
    if principal.role not in settings.security.approval_roles or "ticket:approve" not in principal.scopes:
        raise HTTPException(status_code=403, detail="承認権限がありません")
    try:
        return await service.approve(run_id, principal.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(settings.root / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=str(settings.application["api_host"]), port=int(os.getenv("PORT", settings.application["api_port"])))

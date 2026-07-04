from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agent_service import AgentService
from app.config import get_settings
from app.security import RateLimiter
from app.storage import JsonStore

settings = get_settings()
store = JsonStore(settings.root / "data", settings.security.audit_retention)
service = AgentService(settings, store)
limiter = RateLimiter(settings.security.rate_limit_per_minute)
app = FastAPI(title=settings.application["name"], version="1.0.0")


class UserContext(BaseModel):
    id: str = "employee-001"
    role: str = "employee"
    department: str = "general"


class RunRequest(BaseModel):
    input: str = Field(min_length=3, max_length=8000)
    user: UserContext = Field(default_factory=UserContext)


class ApprovalRequest(BaseModel):
    approver: str = Field(min_length=2, max_length=100)


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "openai": "configured" if settings.has_usable_api_key else "invalid_test_key",
        "mcp": settings.mcp.url,
        "agents": len(settings.agents),
        "model": settings.openai.model,
    }


@app.get("/api/config")
async def public_config() -> dict[str, object]:
    return {
        "model": settings.openai.model,
        "mcpEndpoint": settings.mcp.url,
        "allowedTools": settings.mcp.tools.allowlist,
        "blockedTools": settings.mcp.tools.blocklist,
        "approvalRequired": settings.mcp.tools.approval_required,
    }


@app.get("/api/audit")
async def audit_log() -> list[dict[str, object]]:
    return list(reversed(store.read("audit.json")[-100:]))


@app.post("/api/runs", status_code=201)
async def create_run(payload: RunRequest) -> dict[str, object]:
    if payload.user.role not in settings.security.permitted_roles:
        raise HTTPException(status_code=403, detail="role is not permitted")
    if not limiter.allow(payload.user.id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    if not settings.has_usable_api_key:
        raise HTTPException(status_code=503, detail="有効なOPENAI_API_KEYが必要です。現在はテスト用ダミー値です。")
    try:
        return await service.start(payload.input, payload.user.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AgentまたはMCPの実行に失敗しました") from exc


@app.post("/api/runs/{run_id}/approve")
async def approve_run(run_id: str, payload: ApprovalRequest) -> dict[str, object]:
    try:
        return await service.approve(run_id, payload.approver)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(settings.root / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=str(settings.application["api_host"]), port=int(os.getenv("PORT", settings.application["api_port"])))

"""FastAPIアプリケーションの入口。

用途:
    ブラウザへ画面を配信し、ログイン、Agent実行、承認、監査などのHTTP Endpointを提供する。
必要な理由:
    UIからの入力を直接AIやMCPへ渡さず、認証・CSRF・RBAC/ABACを一か所で検証するため。
関連ファイル:
    ``index.html`` からリクエストを受け、``app/auth.py`` で本人確認し、
    ``app/agent_service.py`` へ検証済みユーザー情報と依頼を渡す。
    設定は ``app/config.py``、監査保存は ``app/storage.py`` を利用する。
"""

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
app = FastAPI(
    title=settings.application["name"],
    version="1.0.0",
    description=(
        "企業向けAIエージェントのAPI。HttpOnlyセッション、CSRF、RBAC/ABACを検証してから、"
        "OpenAI Agents SDKと分離されたMCPサーバーを呼び出します。"
    ),
    openapi_tags=[
        {"name": "Authentication", "description": "ログイン、セッション確認、ログアウト。"},
        {"name": "Operations", "description": "Agent実行とHuman-in-the-loop承認。"},
        {"name": "Governance", "description": "監査、承認キュー、公開可能な実行設定。"},
        {"name": "System", "description": "死活監視とJavaScript UI配信。"},
    ],
)


class LoginRequest(BaseModel):
    """ローカル認証用のログイン入力。パスワードは監査ログへ保存しない。"""

    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=200)


class RunRequest(BaseModel):
    """Agentへ渡す依頼本文。ユーザー属性は本文ではなくセッションから取得する。"""

    input: str = Field(min_length=3, max_length=8000)


def current_session(
    agent_session: str | None = Cookie(default=None, alias=settings.auth.cookie_name),
) -> tuple[Principal, str]:
    """HttpOnly Cookieを検証し、信頼済みユーザー属性とCSRF値を復元する。"""
    if not agent_session:
        raise HTTPException(status_code=401, detail="ログインが必要です")
    try:
        return auth.read_session(agent_session)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="セッションが無効または期限切れです") from exc


def current_user(session: tuple[Principal, str] = Depends(current_session)) -> Principal:
    """読み取りAPI向けに、検証済みセッションからユーザーだけを返す。"""
    return session[0]


def csrf_user(
    session: tuple[Principal, str] = Depends(current_session),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> Principal:
    """更新API向けにセッションとX-CSRF-Tokenを両方検証する。"""
    principal, expected = session
    if not csrf_header or csrf_header != expected:
        raise HTTPException(status_code=403, detail="CSRF検証に失敗しました")
    return principal


@app.post(
    "/api/auth/login",
    tags=["Authentication"],
    summary="ログインしてセッションを開始",
    description=(
        "メールアドレスとパスワードをPBKDF2で検証します。成功時は署名済みセッションを"
        "HttpOnly・SameSite=Strict Cookieへ設定し、更新APIで使うCSRFトークンと公開可能な"
        "ユーザー属性を返します。IPとメールアドレス単位でログイン試行を制限します。"
    ),
    responses={401: {"description": "認証情報が不正"}, 429: {"description": "ログイン試行上限超過"}},
)
async def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, object]:
    """認証成功・失敗を監査し、パスワードを保持せずセッションを発行する。"""
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


@app.get(
    "/api/auth/me",
    tags=["Authentication"],
    summary="現在のログイン情報を取得",
    description="セッションCookieを検証し、role、tenant、department、clearance、scopeとCSRFトークンを返します。",
    responses={401: {"description": "未ログインまたはセッション期限切れ"}},
)
async def me(session: tuple[Principal, str] = Depends(current_session)) -> dict[str, object]:
    """画面再読み込み時にログイン状態と認可属性を復元する。"""
    principal, csrf = session
    return {"user": principal.model_dump(), "csrfToken": csrf}


@app.post(
    "/api/auth/logout",
    tags=["Authentication"],
    summary="ログアウト",
    description="セッションとCSRFを検証後、セッションCookieを削除してログアウト操作を監査します。",
    responses={401: {"description": "未ログイン"}, 403: {"description": "CSRF検証失敗"}},
)
async def logout(response: Response, principal: Principal = Depends(csrf_user)) -> dict[str, bool]:
    """ブラウザの認証Cookieを無効化する。"""
    response.delete_cookie(settings.auth.cookie_name, path="/")
    store.audit("auth", principal.id, "logout", {})
    return {"ok": True}


@app.get(
    "/api/health",
    tags=["System"],
    summary="APIの死活状態を確認",
    description=(
        "認証不要のReadiness情報です。OpenAIキーが実キー形式か、MCP接続先、Agent数、"
        "モデル名を返します。秘密値や接続トークンは返しません。"
    ),
)
async def health() -> dict[str, object]:
    """ロードバランサーや運用監視が利用できる安全な状態情報を返す。"""
    return {
        "status": "ok",
        "openai": "configured" if settings.has_usable_api_key else "invalid_test_key",
        "mcp": {name: server.url for name, server in settings.mcp.servers.items()},
        "agents": len(settings.agents),
        "model": settings.openai.model,
    }


@app.get(
    "/api/config",
    tags=["Governance"],
    summary="公開可能なAgent・MCP設定を取得",
    description="モデル、MCP URL、Toolのallowlist・blocklist、承認対象だけを返し、秘密情報は除外します。",
)
async def public_config() -> dict[str, object]:
    """画面表示や運用確認向けに非秘密設定を整形する。"""
    servers = settings.mcp.servers
    return {
        "model": settings.openai.model,
        "mcpServers": {name: server.url for name, server in servers.items()},
        "allowedTools": [tool for server in servers.values() for tool in server.tools.allowlist],
        "blockedTools": [tool for server in servers.values() for tool in server.tools.blocklist],
        "approvalRequired": [tool for server in servers.values() for tool in server.tools.approval_required],
    }


@app.get(
    "/api/audit",
    tags=["Governance"],
    summary="監査ログを取得",
    description=(
        "audit:read scopeを持つユーザーだけが利用できます。ログインユーザーと同じtenantに属する"
        "Agent実行、MCP検索、承認、更新操作を新しい順に最大100件返します。"
    ),
    responses={401: {"description": "未ログイン"}, 403: {"description": "audit:read scopeなし"}},
)
async def audit_log(principal: Principal = Depends(current_user)) -> list[dict[str, object]]:
    """tenant境界を越える監査イベントを除外して返す。"""
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


@app.get(
    "/api/approvals",
    tags=["Governance"],
    summary="承認待ち一覧を取得",
    description=(
        "manager・finance・adminかつticket:approve scopeを持つユーザーへ、同じtenantの承認待ちだけを返します。"
        "承認権限がない場合は情報漏えいを避けるため空配列を返します。"
    ),
    responses={401: {"description": "未ログイン"}},
)
async def pending_approvals(principal: Principal = Depends(current_user)) -> list[dict[str, object]]:
    """永続化されたRunStateから公開可能な承認メタデータだけを抽出する。"""
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


@app.post(
    "/api/runs",
    status_code=201,
    tags=["Operations"],
    summary="AIエージェントを実行",
    description=(
        "セッション、CSRF、role、ユーザー単位rate limit、OpenAIキーを検証します。依頼文をPIIマスキング後、"
        "Supervisor Agentへ渡します。読み取りはKnowledge MCP、更新候補はTicket MCPへ委任され、"
        "更新Toolが選ばれた場合はRunStateを保存してawaiting_approvalを返します。"
    ),
    responses={
        401: {"description": "未ログイン"},
        403: {"description": "CSRFまたはrole検証失敗"},
        429: {"description": "ユーザー単位の実行上限超過"},
        502: {"description": "OpenAI AgentまたはMCP実行失敗"},
        503: {"description": "有効なOpenAI APIキー未設定"},
    },
)
async def create_run(payload: RunRequest, principal: Principal = Depends(csrf_user)) -> dict[str, object]:
    """ログイン属性を自己申告入力から分離し、信頼済みcontextとしてAgentへ渡す。"""
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


@app.post(
    "/api/runs/{run_id}/approve",
    tags=["Operations"],
    summary="承認待ちAgent実行を承認して再開",
    description=(
        "承認者のセッション、CSRF、approval role、ticket:approve scope、tenant一致を検証します。"
        "保存されたOpenAI Agents SDKのRunStateを復元し、対象Tool呼び出しを承認して同じ実行を再開します。"
    ),
    responses={
        401: {"description": "未ログイン"},
        403: {"description": "承認権限なし、CSRF失敗、またはtenant不一致"},
        404: {"description": "指定run_idの承認待ちが存在しない"},
    },
)
async def approve_run(run_id: str, principal: Principal = Depends(csrf_user)) -> dict[str, object]:
    """承認者IDとroleを監査し、元の依頼者権限のまま中断実行を再開する。"""
    if principal.role not in settings.security.approval_roles or "ticket:approve" not in principal.scopes:
        raise HTTPException(status_code=403, detail="承認権限がありません")
    try:
        return await service.approve(run_id, principal.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get(
    "/",
    tags=["System"],
    summary="JavaScript UIを配信",
    description="ログイン画面、Agent実行画面、承認キューを含むindex.htmlを返します。",
    include_in_schema=False,
)
async def index() -> FileResponse:
    """単一HTMLのフロントエンドを同一オリジンで配信する。"""
    return FileResponse(settings.root / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=str(settings.application["api_host"]), port=int(os.getenv("PORT", settings.application["api_port"])))

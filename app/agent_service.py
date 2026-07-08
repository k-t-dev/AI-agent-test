"""OpenAI Agents SDKを使うAgent実行サービス。

用途:
    Supervisorと専門Agentを構築し、MCP接続、承認中断、保存したRunStateの再開を管理する。
必要な理由:
    HTTP処理とAIの実行制御を分離し、Agentの役割・承認・接続終了を一貫して扱うため。
関連ファイル:
    ``main.py`` から呼ばれ、``app/config.py`` のYAML設定でAgentを組み立てる。
    ``app/auth.py`` でMCP用トークンを作り、``mcp_knowledge_server.py`` と
    ``mcp_ticket_server.py`` へ接続する。承認待ちは ``app/storage.py`` で保存する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agents import Agent, RunState, Runner
from agents.mcp import MCPServerStreamableHttp, MCPToolMetaContext

from app.auth import AuthManager
from app.config import Settings
from app.security import mask_pii
from app.storage import JsonStore


@dataclass
class AgentRuntime:
    """1回のAgent実行で共有するSupervisorと接続対象MCPサーバー。"""
    supervisor: Agent
    mcp_servers: list[MCPServerStreamableHttp]


class AgentService:
    """OpenAI Agents SDKの実行ライフサイクルと監査証跡を管理する。"""
    def __init__(self, settings: Settings, store: JsonStore) -> None:
        self.settings = settings
        self.store = store
        self.auth = AuthManager(settings)

    def _resolve_mcp_meta(self, context: MCPToolMetaContext) -> dict[str, str] | None:
        """信頼済みRun Contextを短寿命MCP認証トークンへ変換する。"""
        run_context = context.run_context.context or {}
        user = run_context.get("user")
        if not user:
            return None
        return {"auth_token": self.auth.issue_mcp_token(user, run_context.get("run_id"))}

    def _runtime(self) -> AgentRuntime:
        """YAML設定から分離されたMCPとSupervisor・専門Agentを毎実行生成する。"""
        cfg = self.settings
        servers: dict[str, MCPServerStreamableHttp] = {}
        for server_id, server_cfg in cfg.mcp.servers.items():
            approvals = {name: "always" for name in server_cfg.tools.approval_required}
            approvals.update({name: "never" for name in server_cfg.tools.allowlist if name not in approvals})
            servers[server_id] = MCPServerStreamableHttp(
                name=server_cfg.server_name,
                params={"url": server_cfg.url, "timeout": server_cfg.timeout_seconds},
                require_approval=approvals,
                cache_tools_list=True,
                tool_meta_resolver=self._resolve_mcp_meta,
            )
        research = Agent(
            name=cfg.agents["research"].name,
            instructions=cfg.agents["research"].instructions,
            model=cfg.openai.model,
            mcp_servers=[servers["knowledge"]],
        )
        action = Agent(
            name=cfg.agents["action"].name,
            instructions=cfg.agents["action"].instructions,
            model=cfg.openai.model,
            mcp_servers=[servers["ticketing"]],
        )
        review = Agent(
            name=cfg.agents["review"].name,
            instructions=cfg.agents["review"].instructions,
            model=cfg.openai.model,
        )
        supervisor = Agent(
            name=cfg.agents["supervisor"].name,
            instructions=cfg.agents["supervisor"].instructions,
            model=cfg.openai.model,
            tools=[
                research.as_tool(tool_name="research_policy", tool_description="社内規程をMCPで調査する"),
                action.as_tool(tool_name="perform_business_action", tool_description="承認付き業務操作をMCPで実行する"),
                review.as_tool(tool_name="review_plan", tool_description="回答と操作計画を安全性レビューする"),
            ],
        )
        return AgentRuntime(supervisor=supervisor, mcp_servers=list(servers.values()))

    async def _connect(self, runtime: AgentRuntime) -> None:
        """全MCPへ接続し、途中失敗時は接続済みサーバーを逆順で閉じる。"""
        connected: list[MCPServerStreamableHttp] = []
        try:
            for server in runtime.mcp_servers:
                await server.connect()
                connected.append(server)
        except Exception:
            for server in reversed(connected):
                await server.cleanup()
            raise

    async def _cleanup(self, runtime: AgentRuntime) -> None:
        """利用したMCP接続を逆順で安全に閉じる。"""
        for server in reversed(runtime.mcp_servers):
            await server.cleanup()

    async def start(self, user_input: str, user: dict[str, str]) -> dict[str, Any]:
        """新規依頼を実行し、更新ToolがあればRunStateを永続化して承認待ちにする。"""
        run_id = str(uuid4())
        safe_input = mask_pii(user_input)
        self.store.audit(run_id, "Supervisor Agent", "accepted", {"user": user, "input": safe_input})
        runtime = self._runtime()
        await self._connect(runtime)
        try:
            result = await Runner.run(
                runtime.supervisor,
                safe_input,
                context={"run_id": run_id, "user": user},
                max_turns=self.settings.openai.max_turns,
            )
            timeline = [{
                "at": datetime.now(UTC).isoformat(),
                "agent": "Supervisor Agent",
                "status": "completed" if not result.interruptions else "approval_required",
                "message": "人間承認待ちです。" if result.interruptions else "OpenAI Agentの処理が完了しました。",
            }]
            response = {
                "id": run_id,
                "status": "awaiting_approval" if result.interruptions else "completed",
                "answer": str(result.final_output or "承認後に処理を続行します。"),
                "timeline": timeline,
                "sources": [],
            }
            if result.interruptions:
                self.store.upsert("pending_runs.json", "runId", {
                    "runId": run_id,
                    "state": result.to_state().to_string(),
                    "createdAt": datetime.now(UTC).isoformat(),
                    "agentVersion": "1.0.0",
                    "tenantId": user["tenant_id"],
                    "requestedBy": user["id"],
                })
                self.store.audit(run_id, "OpenAI Agents SDK", "approval_required", {
                    "tools": [item.name for item in result.interruptions]
                })
                await self._cleanup(runtime)
            else:
                await self._cleanup(runtime)
                self.store.audit(run_id, "Supervisor Agent", "completed", {"answer": response["answer"]})
            return response
        except Exception:
            await self._cleanup(runtime)
            self.store.audit(run_id, "Supervisor Agent", "failed", {"reason": "agent_execution_failed"})
            raise

    async def approve(self, run_id: str, approver: dict[str, Any]) -> dict[str, Any]:
        """tenant一致を確認し、保存済みRunStateへ承認判断を適用して再開する。"""
        pending = next((row for row in self.store.read("pending_runs.json") if row["runId"] == run_id), None)
        if not pending:
            raise KeyError("承認待ちの実行がありません")
        if pending["tenantId"] != approver["tenant_id"]:
            raise PermissionError("tenant boundary violation")
        runtime = self._runtime()
        await self._connect(runtime)
        try:
            state = await RunState.from_string(runtime.supervisor, pending["state"])
            for interruption in state.get_interruptions():
                state.approve(interruption)
            self.store.audit(run_id, "Human Approver", "approved", {
                "approverId": approver["id"],
                "role": approver["role"],
            })
            result = await Runner.run(runtime.supervisor, state, max_turns=self.settings.openai.max_turns)
            if result.interruptions:
                self.store.upsert("pending_runs.json", "runId", {
                    **pending,
                    "state": result.to_state().to_string(),
                    "updatedAt": datetime.now(UTC).isoformat(),
                })
                return {"id": run_id, "status": "awaiting_approval", "answer": "追加承認が必要です。", "sources": [], "timeline": []}
            self.store.delete("pending_runs.json", "runId", run_id)
            self.store.audit(run_id, "Supervisor Agent", "completed", {"answer": str(result.final_output)})
            return {
                "id": run_id,
                "status": "completed",
                "answer": str(result.final_output),
                "sources": [],
                "timeline": [{"at": datetime.now(UTC).isoformat(), "agent": "Human Approver", "status": "approved", "message": f"{approver['display_name']}が承認しました。"}],
            }
        finally:
            await self._cleanup(runtime)

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const ROOT = __dirname;
const DATA = path.join(ROOT, "data");
const PORT = Number(process.env.PORT || 8787);
const allowedTools = new Set(["search_policy", "draft_ticket"]);
const blockedTools = new Set(["raw_data_export", "delete_ticket"]);
const runs = new Map();

const readJson = (name) => JSON.parse(fs.readFileSync(path.join(DATA, name), "utf8"));
const writeJson = (name, value) => fs.writeFileSync(path.join(DATA, name), `${JSON.stringify(value, null, 2)}\n`);

function send(res, status, body, headers = {}) {
  const payload = typeof body === "string" ? body : JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", ...headers });
  res.end(payload);
}

function audit(runId, actor, event, detail = {}) {
  const logs = readJson("audit.json");
  const entry = { id: crypto.randomUUID(), at: new Date().toISOString(), runId, actor, event, detail };
  logs.push(entry);
  writeJson("audit.json", logs.slice(-500));
  return entry;
}

function maskPii(text) {
  return text
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[EMAIL_MASKED]")
    .replace(/\b0\d{1,4}-\d{1,4}-\d{3,4}\b/g, "[PHONE_MASKED]")
    .replace(/\b(?:\d[ -]*?){13,16}\b/g, "[CARD_MASKED]");
}

function searchPolicy(query) {
  const terms = query.toLowerCase().split(/[\s、。？?]+/).filter(Boolean);
  return readJson("policies.json")
    .map((policy) => ({
      ...policy,
      score: policy.keywords.reduce((score, keyword) => score + (query.includes(keyword) ? 2 : 0), 0)
        + terms.reduce((score, term) => score + (policy.body.toLowerCase().includes(term) ? 1 : 0), 0)
    }))
    .filter((policy) => policy.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 3);
}

function callTool(name, args, context = {}) {
  if (blockedTools.has(name) || !allowedTools.has(name)) throw new Error(`Tool '${name}' is not allowed`);
  if (name === "search_policy") return { results: searchPolicy(maskPii(String(args.query || ""))) };
  if (name === "draft_ticket") {
    if (!context.approved) throw new Error("approval_required");
    const tickets = readJson("tickets.json");
    const ticket = {
      id: `EXP-${String(tickets.length + 1).padStart(4, "0")}`,
      status: "draft",
      title: maskPii(String(args.title || "経費申請")),
      description: maskPii(String(args.description || "")),
      createdAt: new Date().toISOString(),
      approvedBy: context.approvedBy
    };
    tickets.push(ticket);
    writeJson("tickets.json", tickets);
    return ticket;
  }
}

function step(run, agent, status, message) {
  run.timeline.push({ at: new Date().toISOString(), agent, status, message });
  audit(run.id, agent, status, { message });
}

function startRun(input, user) {
  const run = {
    id: crypto.randomUUID(),
    user: { id: user?.id || "employee-001", role: user?.role || "employee", department: user?.department || "sales" },
    input: maskPii(input),
    status: "running",
    createdAt: new Date().toISOString(),
    timeline: []
  };
  runs.set(run.id, run);
  step(run, "Supervisor Agent", "accepted", "依頼を受け付け、低リスクの規程検索と承認対象の下書き作成に分解しました。");
  step(run, "Planner Agent", "planned", "Research Agentへ規程検索、Action Agentへ下書き候補の準備を割り当てました。");
  step(run, "Policy Engine", "authorized", `RBAC/ABAC確認済み: ${run.user.role} / ${run.user.department}`);
  const result = callTool("search_policy", { query: run.input });
  audit(run.id, "MCP:company-knowledge", "tool_called", { tool: "search_policy", count: result.results.length });
  step(run, "Research Agent", "researched", `${result.results.length}件の社内規程をMCP経由で取得しました。`);
  run.sources = result.results.map(({ id, title, body, score }) => ({ id, title, body, score }));
  run.answer = run.sources[0]?.body || "該当する社内規程を確認できませんでした。担当部門へ確認してください。";
  const wantsTicket = /(チケット|申請|作って|作成)/.test(run.input);
  if (wantsTicket && run.sources.length) {
    step(run, "Review Agent", "reviewed", "PII、Tool allowlist、入力スキーマを確認しました。書き込み操作のため承認が必要です。");
    step(run, "Supervisor Agent", "approval_required", "draft_ticketは副作用があるため、人間承認まで実行を停止しました。");
    run.status = "awaiting_approval";
    run.pendingAction = {
      tool: "draft_ticket",
      arguments: { title: "出張タクシー代の経費申請", description: `${run.answer}\n\n申請者メモ: ${run.input}` }
    };
  } else {
    step(run, "Supervisor Agent", "completed", "根拠を確認し、質問回答のみで完了しました。");
    run.status = "completed";
  }
  return run;
}

function approveRun(run, approver) {
  if (!run || run.status !== "awaiting_approval") throw new Error("承認待ちの実行ではありません");
  step(run, "Human Approver", "approved", `${approver}が下書き作成を承認しました。`);
  const ticket = callTool(run.pendingAction.tool, run.pendingAction.arguments, { approved: true, approvedBy: approver });
  audit(run.id, "MCP:ticketing", "tool_called", { tool: "draft_ticket", ticketId: ticket.id });
  step(run, "Action Agent", "executed", `MCP経由でチケット${ticket.id}を下書き作成しました。`);
  step(run, "Supervisor Agent", "completed", "操作結果と監査ログを確認し、実行を完了しました。");
  run.ticket = ticket;
  run.status = "completed";
  delete run.pendingAction;
  return run;
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 100_000) req.destroy();
    });
    req.on("end", () => {
      try { resolve(body ? JSON.parse(body) : {}); } catch (error) { reject(error); }
    });
    req.on("error", reject);
  });
}

async function handleMcp(req, res) {
  const message = await parseBody(req);
  const reply = (result) => send(res, 200, { jsonrpc: "2.0", id: message.id ?? null, result });
  if (message.method === "initialize") return reply({ protocolVersion: "2025-03-26", serverInfo: { name: "enterprise-agent-demo", version: "1.0.0" }, capabilities: { tools: {} } });
  if (message.method === "tools/list") return reply({ tools: [
    { name: "search_policy", description: "社内規程を検索する読み取り専用Tool", inputSchema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] } },
    { name: "draft_ticket", description: "承認後に経費申請チケットの下書きを作るTool", annotations: { readOnlyHint: false }, inputSchema: { type: "object", properties: { title: { type: "string" }, description: { type: "string" } }, required: ["title", "description"] } }
  ] });
  if (message.method === "tools/call") {
    try {
      const output = callTool(message.params?.name, message.params?.arguments || {}, { approved: req.headers["x-demo-approved"] === "true", approvedBy: "mcp-client" });
      return reply({ content: [{ type: "text", text: JSON.stringify(output) }] });
    } catch (error) {
      return reply({ content: [{ type: "text", text: error.message }], isError: true });
    }
  }
  send(res, 400, { jsonrpc: "2.0", id: message.id ?? null, error: { code: -32601, message: "Method not found" } });
}

async function router(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  try {
    if (req.method === "GET" && url.pathname === "/api/health") return send(res, 200, { status: "ok", mcp: "configured", agents: 5 });
    if (req.method === "GET" && url.pathname === "/api/config") return send(res, 200, { mcpEndpoint: "/mcp", allowedTools: [...allowedTools], blockedTools: [...blockedTools], approvalRequired: ["draft_ticket"] });
    if (req.method === "GET" && url.pathname === "/api/audit") return send(res, 200, readJson("audit.json").slice(-100).reverse());
    if (req.method === "POST" && url.pathname === "/api/runs") {
      const body = await parseBody(req);
      if (!body.input || String(body.input).trim().length < 3) return send(res, 422, { error: "inputは3文字以上で指定してください" });
      return send(res, 201, startRun(String(body.input), body.user));
    }
    const runMatch = url.pathname.match(/^\/api\/runs\/([^/]+)$/);
    if (req.method === "GET" && runMatch) return runs.has(runMatch[1]) ? send(res, 200, runs.get(runMatch[1])) : send(res, 404, { error: "run not found" });
    const approvalMatch = url.pathname.match(/^\/api\/runs\/([^/]+)\/approve$/);
    if (req.method === "POST" && approvalMatch) {
      const body = await parseBody(req);
      return send(res, 200, approveRun(runs.get(approvalMatch[1]), body.approver || "demo-manager"));
    }
    if (req.method === "POST" && url.pathname === "/mcp") return handleMcp(req, res);
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      const html = fs.readFileSync(path.join(ROOT, "index.html"));
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      return res.end(html);
    }
    send(res, 404, { error: "not found" });
  } catch (error) {
    send(res, 500, { error: error.message });
  }
}

if (require.main === module) {
  http.createServer(router).listen(PORT, "127.0.0.1", () => {
    console.log(`AI Agent demo: http://127.0.0.1:${PORT}`);
    console.log(`MCP endpoint: http://127.0.0.1:${PORT}/mcp`);
  });
}

module.exports = { router, maskPii, searchPolicy, callTool, startRun, approveRun };

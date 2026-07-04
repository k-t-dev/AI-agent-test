const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { maskPii, searchPolicy, callTool, startRun, approveRun } = require("./server");

const auditPath = path.join(__dirname, "data", "audit.json");
const ticketsPath = path.join(__dirname, "data", "tickets.json");
const initialAudit = fs.readFileSync(auditPath, "utf8");
const initialTickets = fs.readFileSync(ticketsPath, "utf8");

test.after(() => {
  fs.writeFileSync(auditPath, initialAudit);
  fs.writeFileSync(ticketsPath, initialTickets);
});

test("PIIをマスキングする", () => {
  assert.equal(maskPii("a@example.com 090-1234-5678"), "[EMAIL_MASKED] [PHONE_MASKED]");
});

test("RAG検索でタクシー規程を返す", () => {
  assert.equal(searchPolicy("出張のタクシー代")[0].id, "EXP-TRAVEL-004");
});

test("未承認の書き込みToolを拒否する", () => {
  assert.throws(() => callTool("draft_ticket", { title: "x", description: "y" }), /approval_required/);
});

test("Supervisor実行は承認待ちになり、承認後に完了する", () => {
  const run = startRun("出張のタクシー代を確認してチケットを作成", { role: "employee" });
  assert.equal(run.status, "awaiting_approval");
  approveRun(run, "test-manager");
  assert.equal(run.status, "completed");
  assert.equal(run.ticket.status, "draft");
});

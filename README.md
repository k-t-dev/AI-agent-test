# 本番想定AIエージェント デモ

Supervisor Agent、専門Agent、MCP Tool、承認ゲート、監査ログをローカルで体験するデモです。外部APIやAPIキーは使いません。

## 起動

```bash
npm start
```

ブラウザで `http://127.0.0.1:8787` を開き、ページ下部の「実働デモ」から実行します。

## 実装範囲

- Supervisor Agent: 依頼の受付、リスク判断、停止、結果確認
- Planner / Research / Action / Review Agent: 分解、検索、実行、検査
- MCP: `POST /mcp` で `initialize`、`tools/list`、`tools/call` を提供
- Tool境界: `search_policy` と `draft_ticket` のallowlist、危険Toolのblocklist
- 承認: `draft_ticket` は承認前に `approval_required` で停止
- データ保護: メール、電話番号、カード番号をマスキング
- 監査: `data/audit.json` にAgent判断とTool実行を保存
- チケット: 承認後の下書きを `data/tickets.json` に保存

## MCP設定

接続定義は `mcp.json` にあります。デモサーバー起動後のエンドポイントは `http://127.0.0.1:8787/mcp` です。

## テスト

```bash
npm test
```

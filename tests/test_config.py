"""設定ファイルの安全な境界を確認するテスト。

用途:
    MCPのTool分離、承認対象、接続先の分離、ダミーAPIキー判定を確認する。
必要な理由:
    YAMLの編集だけで危険Toolが誤公開される事故を、起動前に検出するため。
関連ファイル:
    ``app/config.py`` が読み込む ``config/agents.yml`` を対象にする。
"""

import os

from app.config import get_settings


def test_yaml_policy_has_safe_tool_boundaries() -> None:
    settings = get_settings()
    knowledge = settings.mcp.servers["knowledge"]
    ticketing = settings.mcp.servers["ticketing"]
    assert knowledge.tools.allowlist == ["search_policy"]
    assert knowledge.tools.approval_required == []
    assert ticketing.tools.allowlist == ["draft_ticket"]
    assert ticketing.tools.approval_required == ["draft_ticket"]
    for server in settings.mcp.servers.values():
        assert set(server.tools.allowlist).isdisjoint(server.tools.blocklist)


def test_mcp_servers_are_isolated() -> None:
    settings = get_settings()
    knowledge = settings.mcp.servers["knowledge"]
    ticketing = settings.mcp.servers["ticketing"]
    assert knowledge.url != ticketing.url
    assert set(knowledge.tools.allowlist).isdisjoint(ticketing.tools.allowlist)


def test_dummy_key_is_not_usable() -> None:
    """実キーを表示・変更せず、ダミー値だけが利用不可になることを確認する。"""
    original = os.environ.get("OPENAI_API_KEY")
    try:
        os.environ["OPENAI_API_KEY"] = "test-only-dummy-key-not-valid"
        get_settings.cache_clear()
        assert get_settings().has_usable_api_key is False
    finally:
        if original is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = original
        get_settings.cache_clear()

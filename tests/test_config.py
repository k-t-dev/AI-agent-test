from app.config import get_settings


def test_yaml_policy_has_safe_tool_boundaries() -> None:
    settings = get_settings()
    assert "search_policy" in settings.mcp.tools.allowlist
    assert "draft_ticket" in settings.mcp.tools.approval_required
    assert set(settings.mcp.tools.allowlist).isdisjoint(settings.mcp.tools.blocklist)


def test_dummy_key_is_not_usable() -> None:
    assert get_settings().has_usable_api_key is False


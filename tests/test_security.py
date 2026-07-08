"""共通セキュリティ処理を確認するテスト。

用途:
    PIIマスキングとrate limitが期待どおり拒否することを確認する。
必要な理由:
    ``main.py``、Agent、MCPで共用する処理なので、変更の影響範囲が広いため。
関連ファイル:
    ``app/security.py`` の公開関数とクラスだけを小さく独立して検証する。
"""

from app.security import RateLimiter, mask_pii


def test_mask_pii() -> None:
    assert mask_pii("a@example.com 090-1234-5678") == "[EMAIL_MASKED] [PHONE_MASKED]"


def test_rate_limiter_fails_closed() -> None:
    limiter = RateLimiter(1)
    assert limiter.allow("user-1") is True
    assert limiter.allow("user-1") is False

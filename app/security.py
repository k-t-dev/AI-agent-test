"""入力データとAPI呼び出し量を守る共通セキュリティ処理。

用途:
    メール・電話・カード番号のマスキングと、1分単位のrate limitを提供する。
必要な理由:
    不要な個人情報をAIへ送らず、連続実行による費用増加やサービス負荷を抑えるため。
関連ファイル:
    ``main.py`` がAPI実行回数を制限し、``app/agent_service.py`` がAI入力をマスクする。
    両MCPサーバーも保存前に同じマスキング処理を利用する。
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from threading import Lock

EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE = re.compile(r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b")
CARD = re.compile(r"\b(?:\d[ -]*?){13,16}\b")


def mask_pii(value: str) -> str:
    """メール、電話番号、カード番号らしき文字列をAgentへ渡す前に置換する。"""
    value = EMAIL.sub("[EMAIL_MASKED]", value)
    value = PHONE.sub("[PHONE_MASKED]", value)
    return CARD.sub("[CARD_MASKED]", value)


class RateLimiter:
    """直近1分のイベント数を制限するスライディングウィンドウ型limiter。"""
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._events: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, subject: str) -> bool:
        """対象IDが上限内なら記録してTrue、超過時はFalseを返す。"""
        cutoff = datetime.now(UTC) - timedelta(minutes=1)
        with self._lock:
            events = self._events[subject]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(datetime.now(UTC))
            return True

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4


class JsonStore:
    def __init__(self, data_dir: Path, audit_retention: int = 5000) -> None:
        self.data_dir = data_dir
        self.audit_retention = audit_retention
        self._lock = Lock()
        data_dir.mkdir(parents=True, exist_ok=True)

    def read(self, filename: str) -> list[dict[str, Any]]:
        path = self.data_dir / filename
        if not path.exists():
            return []
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def append(self, filename: str, item: dict[str, Any], limit: int | None = None) -> None:
        path = self.data_dir / filename
        with self._lock:
            rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            rows.append(item)
            if limit:
                rows = rows[-limit:]
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def upsert(self, filename: str, key: str, item: dict[str, Any]) -> None:
        path = self.data_dir / filename
        with self._lock:
            rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            rows = [row for row in rows if row.get(key) != item.get(key)]
            rows.append(item)
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def delete(self, filename: str, key: str, value: str) -> None:
        path = self.data_dir / filename
        with self._lock:
            rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            rows = [row for row in rows if row.get(key) != value]
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def audit(self, run_id: str, actor: str, event: str, detail: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "id": str(uuid4()),
            "at": datetime.now(UTC).isoformat(),
            "runId": run_id,
            "actor": actor,
            "event": event,
            "detail": detail,
        }
        self.append("audit.json", entry, self.audit_retention)
        return entry

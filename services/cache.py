from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class TTLCache:
    default_ttl_seconds: int = 300

    def __post_init__(self):
        self._store: Dict[str, Any] = {}
        self._exp: Dict[str, float] = {}

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        exp = self._exp.get(key)
        if exp is None:
            return None
        if now > exp:
            return None
        return self._store.get(key)

    def get_stale(self, key: str) -> Optional[Any]:
        return self._store.get(key)

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        self._store[key] = value
        self._exp[key] = time.time() + ttl
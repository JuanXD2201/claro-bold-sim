"""Registro en memoria de llamadas a la API simulada (para la consola)."""
import threading
from collections import deque
from datetime import datetime, timezone


class ApiLog:
    def __init__(self, maxlen=500):
        self._items = deque(maxlen=maxlen)
        self._seq = 0
        self._lock = threading.Lock()

    def add(self, **entry):
        with self._lock:
            self._seq += 1
            entry["id"] = self._seq
            entry["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            self._items.append(entry)
            return entry

    def list(self, since=0, limit=200):
        with self._lock:
            items = [e for e in self._items if e["id"] > since]
        return items[-limit:]

    def clear(self):
        with self._lock:
            self._items.clear()

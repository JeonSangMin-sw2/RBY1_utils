from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(slots=True)
class SessionAuthority:
    ttl_seconds: float = 60.0
    bootstrap_token: str = field(default_factory=new_token)
    issued_at: float = field(default_factory=time.monotonic)
    _used: bool = False
    _sessions: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def exchange(self, proof: str, *, now: float | None = None) -> str | None:
        current = time.monotonic() if now is None else now
        with self._lock:
            valid = (
                not self._used
                and current - self.issued_at <= self.ttl_seconds
                and hmac.compare_digest(proof, self.bootstrap_token)
            )
            if not valid:
                return None
            self._used = True
            token = new_token()
            self._sessions.add(token)
            return token

    def accepts(self, token: str) -> bool:
        with self._lock:
            return any(hmac.compare_digest(token, candidate) for candidate in self._sessions)


def exact_loopback_headers(host: str | None, origin: str | None, port: int) -> bool:
    expected_host = f"127.0.0.1:{port}"
    return host == expected_host and origin == f"http://{expected_host}"

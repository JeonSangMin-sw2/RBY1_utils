from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass


def _send_signal(process_group: int, signal_number: signal.Signals) -> None:
    kill_group = getattr(os, "killpg", None)
    if kill_group is not None:
        kill_group(process_group, signal_number)
        return
    os.kill(process_group, signal_number)


@dataclass(slots=True)
class CancellationController:
    process_group: int
    requested_at: float

    @classmethod
    def request(cls, process_group: int) -> "CancellationController":
        controller = cls(process_group, time.monotonic())
        _send_signal(process_group, signal.SIGTERM)
        return controller

    def escalate(self, *, now: float | None = None) -> str | None:
        elapsed = (time.monotonic() if now is None else now) - self.requested_at
        if elapsed >= 10:
            _send_signal(self.process_group, getattr(signal, "SIGKILL", signal.SIGTERM))
            return "killed"
        if elapsed >= 5:
            _send_signal(self.process_group, signal.SIGTERM)
            return "terminated"
        return None

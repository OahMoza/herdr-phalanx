"""Callback / Herdr subprocess runners.

Pure subprocess invocation, hidden behind a tiny interface so the Core can
be tested with a fake. `shell=False` is enforced everywhere except the
explicitly legacy `ShellTemplateCommandRunner`, which keeps the previous
"`command_template` with `{message_id}` placeholders" contract intact.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import time
from typing import List, Sequence


@dataclasses.dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    command: list[str]
    started_at: str
    ended_at: str

    def to_dict(self) -> dict:
        return {
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "command": list(self.command),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


class SubprocessCommandRunner:
    """Production runner for executable callbacks. Always argv-based,
    `shell=False`."""

    def run(self, command: Sequence[str], *, timeout: int) -> CommandResult:
        if not command:
            raise ValueError("command must be a non-empty argv list")
        cmd = [str(x) for x in command]
        started = _now_iso()
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            env=os.environ.copy(),
            check=False,
        )
        ended = _now_iso()
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            command=list(cmd),
            started_at=started,
            ended_at=ended,
        )


class ShellTemplateCommandRunner:
    """Legacy runner for `command_template` callbacks. Uses `shell=True`
    so existing `{message_id}` template strings keep working. New
    callbacks should register with `--executable` + `--arguments` and
    use `SubprocessCommandRunner` instead."""

    def run(self, command: Sequence[str], *, timeout: int) -> CommandResult:
        if not command:
            raise ValueError("command must be a non-empty argv list")
        cmd_str = " ".join(str(x) for x in command)
        started = _now_iso()
        completed = subprocess.run(
            cmd_str,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=True,
            env=os.environ.copy(),
            check=False,
        )
        ended = _now_iso()
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            command=list(command),
            started_at=started,
            ended_at=ended,
        )


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now().isoformat(timespec="seconds")

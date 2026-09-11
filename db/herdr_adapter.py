"""Herdr Adapter facade.

Builds the lease prompt delivered to a TUI Worker and translates the
"deliver the prompt to a waiting pane" operation into a small interface.
This module owns the *only* place that knows about Herdr's `agent prompt`
command and which fields are in/out of bounds for a leased Worker.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

DEFAULT_LEASE_PROMPT_HEADER = (
    "# Bus Lease (RESTRICTED)\n\n"
    "You hold exactly ONE Bus Message. Do NOT call `enqueue` or `claim`.\n"
    "Use ONLY the commands listed in this prompt.\n"
)


@dataclasses.dataclass
class CommandResult:
    ok: bool
    output: dict
    error: Optional[str] = None
    raw_stdout: str = ""
    raw_stderr: str = ""


class HerdrCommanderRunner:
    """Production Herdr Adapter: shells out to `herdr agent prompt`.

    Hard-coded argv list, no shell, env-cleaned path. Anything beyond
    "deliver this prompt and report whether it started" is the caller's
    concern."""

    def __init__(self, herdr_bin: Optional[str] = None,
                 env: Optional[dict] = None) -> None:
        self._herdr_bin = herdr_bin or _find_herdr()
        self._env = env

    def run_agent_prompt(
        self,
        *,
        agent_name: str,
        prompt: str,
        timeout: int,
    ) -> CommandResult:
        if not self._herdr_bin:
            return CommandResult(ok=False, output={},
                                 error="herdr binary not found on PATH")
        cmd = [
            self._herdr_bin, "agent", "prompt",
            agent_name,
            prompt,
        ]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                env=self._env or os.environ.copy(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return CommandResult(ok=False, output={},
                                 error=f"herdr agent prompt timed out after {timeout}s",
                                 raw_stderr=stderr or "")
        except FileNotFoundError:
            return CommandResult(ok=False, output={},
                                 error=f"herdr binary missing: {self._herdr_bin}")
        if completed.returncode != 0:
            return CommandResult(
                ok=False,
                output={},
                error=(completed.stderr or completed.stdout or "").strip() or
                       f"herdr exit {completed.returncode}",
                raw_stdout=completed.stdout or "",
                raw_stderr=completed.stderr or "",
            )
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            payload = {"raw_stdout": completed.stdout or ""}
        return CommandResult(ok=True, output=payload,
                             raw_stdout=completed.stdout or "",
                             raw_stderr=completed.stderr or "")


def _find_herdr() -> Optional[str]:
    found = shutil.which("herdr")
    return found


# ---------------------------------------------------------------------------
# Lease prompt construction (pure functions, easy to test)
# ---------------------------------------------------------------------------


def build_lease_prompt(
    message: dict,
    *,
    agent_kind: str,
    profile: Optional[str],
    python_executable: str,
    db_module: str,
    header: str = DEFAULT_LEASE_PROMPT_HEADER,
) -> str:
    """Render the prompt that is delivered to a waiting TUI Worker. The first
    line of the header is reserved for the restriction notice; the body lists
    the four allowed CLI calls, the `complete`/`fail`/`cancelled` parameters
    they expect, and a copy of the inbound payload."""

    payload = message.get("payload") or {}
    message_id = message.get("id", "")
    lease_id = message.get("lease_id", "")
    attempts = message.get("attempts", 1)
    artifact_dir = str(Path("~/.herdr-phalanx/runs/agent-bus") /
                       message_id / f"attempt-{attempts}")
    artifact_hint = (
        f"\nWrite your raw report to `{artifact_dir}/raw.txt` (use\n"
        f"`mkdir -p` first) and a structured result JSON to\n"
        f"`{artifact_dir}/result.json`. The `complete` command below\n"
        f"reads both files by path.\n"
    )

    complete_cmd = _render_cmd(python_executable, db_module,
        "complete",
        {"--id": message_id,
         "--worker-id": "${WORKER_ID}",
         "--lease": lease_id,
         "--result-file": "${ARTIFACT_DIR}/result.json",
         "--raw-report-file": "${ARTIFACT_DIR}/raw.txt"})

    fail_cmd = _render_cmd(python_executable, db_module,
        "fail",
        {"--id": message_id,
         "--worker-id": "${WORKER_ID}",
         "--lease": lease_id,
         "--reason": "${REASON}",
         "--raw-report-file": "${ARTIFACT_DIR}/raw.txt"})

    cancelled_cmd = _render_cmd(python_executable, db_module,
        "cancelled",
        {"--id": message_id,
         "--worker-id": "${WORKER_ID}",
         "--lease": lease_id,
         "--reason": "${REASON}",
         "--raw-report-file": "${ARTIFACT_DIR}/raw.txt"})

    heartbeat_cmd = _render_cmd(python_executable, db_module,
        "heartbeat",
        {"--id": message_id,
         "--worker-id": "${WORKER_ID}",
         "--lease": lease_id})

    sections = [
        header,
        f"## Message\n",
        f"- id: `{message_id}`",
        f"- agent_kind: `{agent_kind}`",
        f"- profile: `{profile or ''}`",
        f"- attempts: `{attempts}`",
        f"- lease_id: `{lease_id}`",
        "",
        "## Payload",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Allowed Commands",
        f"Set `WORKER_ID=<your-herdr-agent-name>` and `ARTIFACT_DIR={artifact_dir}`",
        f"(create the directory first), then run exactly one of:",
        f"1. `{heartbeat_cmd}`  (renew the lease; call periodically)",
        f"2. `{complete_cmd}`  (succeed; write result.json + raw.txt first)",
        f"3. `{fail_cmd}`",
        f"4. `{cancelled_cmd}`  (only if the producer already called `cancel` and "
        "`cancel_requested` is true)",
        "",
        artifact_hint,
        "## Forbidden",
        "- `enqueue`, `claim`, `route-set`, `route-delete`, `worker-register`",
        "- Any other CLI subcommand of `agent_bus.py` not listed above",
        "",
        "Reply in your TUI when done.",
    ]
    return "\n".join(sections)


def _render_cmd(python_executable: str, db_module: str,
                subcommand: str, flags: dict[str, str]) -> str:
    parts = [python_executable, db_module, subcommand]
    for flag, value in flags.items():
        parts.append(flag)
        parts.append(value)
    return " ".join(parts)

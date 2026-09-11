#!/usr/bin/env python3
"""Herdr Phalanx Agent Bus CLI.

Thin adapter that translates CLI flags into `AgentBus` Core actions and
serialises the result as JSON. The Core hides SQLite, lease state, the
artifact filesystem, callback subprocess, and the Herdr-specific prompt
construction. This file owns argument parsing, JSON I/O, error-to-exit
mapping, and the `worker-loop` plumbing.

Environment:
  AGENT_BUS_DB          override database path (default ~/.herdr-phalanx/agent-bus.db)
  AGENT_BUS_ARTIFACTS   override raw report directory (default ~/.herdr-phalanx/runs/agent-bus)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import agent_bus_core as core
import herdr_adapter


SCHEMA_PATH = Path(__file__).parent / "agent_bus_schema.sql"


# ---------------------------------------------------------------------------
# Backwards-compatible module-level helpers used by tests and external
# scripts that import `agent_bus` and call `bus._connect()` or `bus._sha256_file()`.
# New code should prefer the `AgentBus` Core interface in `agent_bus_core`.
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return core.AgentBus.default_database_path()


def _artifacts_root() -> Path:
    return core.AgentBus.default_artifacts_root()


def _connect():
    bus = _bus()
    conn = bus._db_open()  # noqa: SLF001 - intentional compatibility shim
    return conn


def _sha256_file(path) -> str:
    from agent_bus_core import _sha256_file as _core_sha256
    return _core_sha256(Path(path))


def _bus() -> core.AgentBus:
    return core.AgentBus(core.AgentBus.default_database_path(),
                         core.AgentBus.default_artifacts_root())


def _out(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _fail(exc: Exception) -> None:
    sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
    sys.exit(1)


def _read_payload_from_arg(args: argparse.Namespace, attr: str) -> Any:
    raw = getattr(args, attr, None)
    if raw is None:
        return None
    return json.loads(raw)


def _read_payload_file(path_str: Optional[str]) -> Any:
    if not path_str:
        return None
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Subcommand handlers — keep output shape identical to the original CLI so
# scripts and templates that consume it continue to work.
# ---------------------------------------------------------------------------


def cmd_init_db(_args: argparse.Namespace) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    _bus().init_database(schema_sql)
    _out({"status": "ok", "db_path": str(core.AgentBus.default_database_path()),
          "schema": str(SCHEMA_PATH)})


def cmd_route_set(args: argparse.Namespace) -> None:
    payload = _bus().set_route(
        agent_kind=args.agent_kind,
        max_in_flight=args.max_in_flight,
        default_lease_seconds=args.default_lease_seconds,
        profile=args.profile,
        enabled=args.enabled,
        actor_id=args.actor,
    )
    _out(payload)


def cmd_route_status(args: argparse.Namespace) -> None:
    payload = _bus().list_routes()
    _out(payload)


def cmd_route_delete(args: argparse.Namespace) -> None:
    payload = _bus().delete_route(args.agent_kind, profile=args.profile,
                                  actor_id=args.actor)
    _out(payload)


def cmd_worker_register(args: argparse.Namespace) -> None:
    metadata = _read_payload_from_arg(args, "metadata") or {}
    payload = _bus().register_worker(
        worker_id=args.worker_id,
        agent_kind=args.agent_kind,
        profile=args.profile,
        agent_name=args.agent_name,
        pane_id=args.pane,
        tab_id=args.tab,
        status=args.status,
        metadata=metadata,
    )
    _out(payload)


def cmd_worker_heartbeat(args: argparse.Namespace) -> None:
    payload = _bus().heartbeat_worker(args.worker_id)
    _out(payload)


def cmd_worker_list(_args: argparse.Namespace) -> None:
    _out(_bus().list_workers())


def cmd_enforce_route_set(args: argparse.Namespace) -> None:
    args.agent_kind = args.kind
    args.max_in_flight = args.max
    args.default_lease_seconds = args.lease
    cmd_route_set(args)


def cmd_enqueue(args: argparse.Namespace) -> None:
    payload = _read_payload_from_arg(args, "payload")
    if payload is None and args.payload_file:
        payload = _read_payload_file(args.payload_file)
    payload = payload or {}
    if args.instruction is not None:
        payload = {**payload, "instruction": args.instruction}
    if args.callback_payload:
        payload = {**payload, "_callback_payload": json.loads(args.callback_payload)}
    msg = _bus().enqueue(
        caller_id=args.caller,
        agent_kind=args.agent_kind,
        payload=payload,
        profile=args.profile,
        priority=args.priority,
        max_attempts=args.max_attempts,
        callback_name=args.callback_name,
    )
    _out(msg)


def cmd_claim(args: argparse.Namespace) -> None:
    msg = _bus().claim(
        worker_id=args.worker_id,
        agent_kind=args.agent_kind,
        profile=args.profile,
        lease_seconds=args.lease_seconds,
    )
    if msg is None:
        # Preserve the original contract: print `null` so the CLI emits a
        # parseable JSON document and `_run_cli` returns `None`.
        print("null")
        return
    _out(msg)


def cmd_heartbeat_message(args: argparse.Namespace) -> None:
    payload = _bus().heartbeat_message(
        message_id=args.id,
        worker_id=args.worker_id,
        lease_id=args.lease,
        lease_seconds=args.lease_seconds,
    )
    _out(payload)


def cmd_complete(args: argparse.Namespace) -> None:
    if not args.result_file:
        raise core.ValidationError("--result-file is required for complete")
    result = _read_payload_file(args.result_file) or {}
    msg = _bus().complete(
        message_id=args.id,
        worker_id=args.worker_id,
        lease_id=args.lease,
        result_payload=result,
        raw_report_path=args.raw_report_file,
    )
    _out(msg)


def cmd_fail(args: argparse.Namespace) -> None:
    msg = _bus().fail(
        message_id=args.id,
        worker_id=args.worker_id,
        lease_id=args.lease,
        reason=args.reason or "",
        raw_report_path=args.raw_report_file,
    )
    _out(msg)


def cmd_cancelled(args: argparse.Namespace) -> None:
    msg = _bus().cancelled(
        message_id=args.id,
        worker_id=args.worker_id,
        lease_id=args.lease,
        reason=args.reason or "",
        raw_report_path=args.raw_report_file,
    )
    _out(msg)


def cmd_cancel(args: argparse.Namespace) -> None:
    msg = _bus().cancel(args.caller, args.id)
    _out(msg)


def cmd_requeue(args: argparse.Namespace) -> None:
    msg = _bus().requeue(args.caller, args.id)
    _out(msg)


def cmd_reap(args: argparse.Namespace) -> None:
    bus = _bus()
    total = 0
    while True:
        one = bus.reap_once()
        if one is None:
            break
        total += 1
        if args.once:
            break
    _out({"reaped": total})


def cmd_result_list(args: argparse.Namespace) -> None:
    payload = _bus().list_results(args.caller, after_event=args.after_event)
    _out(payload)


def cmd_result_get(args: argparse.Namespace) -> None:
    payload = _bus().get_result(args.caller, args.id)
    _out(payload)


def cmd_result_ack(args: argparse.Namespace) -> None:
    payload = _bus().ack_result(args.caller, args.id)
    _out(payload)


def cmd_result_history(args: argparse.Namespace) -> None:
    payload = _bus().result_history(args.caller, args.id)
    _out(payload)


def cmd_callback_register(args: argparse.Namespace) -> None:
    """Two modes:
      * New style: `--executable PATH --arguments '[json-array]'`
        -> Core stores argv; CLI uses `SubprocessCommandRunner` (`shell=False`).
      * Legacy template: `--kind command --command-template "..."`
        -> Core stores the template; CLI uses `ShellTemplateCommandRunner`.
    """
    if args.executable:
        arguments = json.loads(args.arguments) if args.arguments else []
        payload = _bus().register_callback(
            name=args.name,
            executable=args.executable,
            arguments=arguments,
            actor_id=args.actor,
        )
    elif args.command_template:
        payload = _bus().register_legacy_template_callback(
            name=args.name,
            kind=args.kind or "command",
            command_template=args.command_template,
            actor_id=args.actor,
        )
    else:
        raise core.ValidationError(
            "either --executable/--arguments or --command-template is required")
    _out(payload)


def cmd_callback_status(_args: argparse.Namespace) -> None:
    _out(_bus().list_callbacks())


def cmd_callback_deliver(args: argparse.Namespace) -> None:
    """Drain pending deliveries until empty or `--once`."""
    bus = _bus()
    while True:
        delivered = bus.deliver_one_callback()
        if delivered is None:
            break
        if args.once:
            _out({"delivered": [delivered]})
            return
        _out({"delivered": [delivered]})
    _out({"delivered": 0})


def cmd_callback_deliver_one(_args: argparse.Namespace) -> None:
    delivered = _bus().deliver_one_callback()
    if delivered is None:
        _out({"delivered": None})
        return
    _out({"delivered": delivered})


def cmd_worker_loop(args: argparse.Namespace) -> None:
    """Claim one Message and deliver a restricted lease prompt to the
    worker identified by `--agent-name` + `--pane-id` / `--tab-id`."""

    runner = herdr_adapter.HerdrCommanderRunner(herdr_bin=args.herdr_bin)
    payload = _bus().worker_loop_once(
        worker_id=args.worker_id,
        agent_kind=args.agent_kind,
        profile=args.profile,
        agent_name=args.agent_name,
        pane_id=args.pane_id,
        tab_id=args.tab_id,
        commander_runner=runner,
        prompt_builder=lambda msg: herdr_adapter.build_lease_prompt(
            msg,
            agent_kind=args.agent_kind,
            profile=args.profile,
            python_executable=sys.executable,
            db_module=str(Path(__file__).resolve()),
        ),
        lease_seconds=args.lease_seconds,
    )
    _out(payload)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent_bus",
                                 description="Herdr Phalanx Agent Bus CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init-db")
    sp.set_defaults(func=cmd_init_db)

    sp = sub.add_parser("route-set")
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--max-in-flight", type=int, required=True)
    sp.add_argument("--default-lease-seconds", type=int, required=True)
    sp.add_argument("--enabled", type=int, default=1)
    sp.add_argument("--actor", default="operator")
    sp.set_defaults(func=cmd_route_set)

    sp = sub.add_parser("set-route")
    sp.add_argument("--kind", required=True)
    sp.add_argument("--max", type=int, required=True)
    sp.add_argument("--lease", type=int, required=True)
    sp.add_argument("--profile")
    sp.add_argument("--enabled", type=int, default=1)
    sp.set_defaults(func=cmd_enforce_route_set)

    sp = sub.add_parser("route-status")
    sp.set_defaults(func=cmd_route_status)

    sp = sub.add_parser("route-delete")
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--actor", default="operator")
    sp.set_defaults(func=cmd_route_delete)

    sp = sub.add_parser("worker-register")
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--agent-name")
    sp.add_argument("--pane")
    sp.add_argument("--tab")
    sp.add_argument("--status", default="ready")
    sp.add_argument("--metadata", default="{}")
    sp.set_defaults(func=cmd_worker_register)

    sp = sub.add_parser("worker-heartbeat")
    sp.add_argument("--worker-id", required=True)
    sp.set_defaults(func=cmd_worker_heartbeat)

    sp = sub.add_parser("worker-list")
    sp.set_defaults(func=cmd_worker_list)

    sp = sub.add_parser("enqueue")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--priority", type=int, default=5)
    sp.add_argument("--max-attempts", type=int, default=3)
    sp.add_argument("--payload", help="Inline JSON object string")
    sp.add_argument("--payload-file", help="Path to a JSON file containing the payload")
    sp.add_argument("--instruction")
    sp.add_argument("--callback-name")
    sp.add_argument("--callback-payload")
    sp.set_defaults(func=cmd_enqueue)

    sp = sub.add_parser("claim")
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--lease-seconds", type=int,
                    default=core.DEFAULT_LEASE_SECONDS)
    sp.set_defaults(func=cmd_claim)

    sp = sub.add_parser("heartbeat")
    sp.add_argument("--id", required=True)
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--lease", required=True)
    sp.add_argument("--lease-seconds", type=int,
                    default=core.DEFAULT_LEASE_SECONDS)
    sp.set_defaults(func=cmd_heartbeat_message)

    sp = sub.add_parser("heartbeat-message")
    sp.add_argument("--id", required=True)
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--lease", required=True)
    sp.add_argument("--lease-seconds", type=int,
                    default=core.DEFAULT_LEASE_SECONDS)
    sp.set_defaults(func=cmd_heartbeat_message)

    sp = sub.add_parser("complete")
    sp.add_argument("--id", required=True)
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--lease", required=True)
    sp.add_argument("--result-file", required=True)
    sp.add_argument("--raw-report-file", required=True)
    sp.set_defaults(func=cmd_complete)

    sp = sub.add_parser("fail")
    sp.add_argument("--id", required=True)
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--lease", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--raw-report-file", required=True)
    sp.set_defaults(func=cmd_fail)

    sp = sub.add_parser("cancelled")
    sp.add_argument("--id", required=True)
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--lease", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--raw-report-file", required=True)
    sp.set_defaults(func=cmd_cancelled)

    sp = sub.add_parser("cancel")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_cancel)

    sp = sub.add_parser("requeue")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_requeue)

    sp = sub.add_parser("reap")
    sp.add_argument("--once", action="store_true", default=True)
    sp.add_argument("--actor", default="operator")
    sp.set_defaults(func=cmd_reap)

    sp = sub.add_parser("result-list")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--after-event", type=int, default=0)
    sp.set_defaults(func=cmd_result_list)

    sp = sub.add_parser("result-get")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_result_get)

    sp = sub.add_parser("result-ack")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_result_ack)

    sp = sub.add_parser("result-history")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_result_history)

    sp = sub.add_parser("callback-register")
    sp.add_argument("--name", required=True)
    sp.add_argument("--kind")
    sp.add_argument("--command-template")
    sp.add_argument("--executable")
    sp.add_argument("--arguments")
    sp.add_argument("--enabled", type=int, default=1)
    sp.add_argument("--actor", default="operator")
    sp.set_defaults(func=cmd_callback_register)

    sp = sub.add_parser("callback-status")
    sp.set_defaults(func=cmd_callback_status)

    sp = sub.add_parser("callback-list")
    sp.set_defaults(func=cmd_callback_status)

    sp = sub.add_parser("callback-deliver")
    sp.add_argument("--once", action="store_true", default=True)
    sp.set_defaults(func=cmd_callback_deliver)

    sp = sub.add_parser("callback-deliver-one")
    sp.set_defaults(func=cmd_callback_deliver_one)

    sp = sub.add_parser("worker-loop")
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--agent-name", required=True)
    sp.add_argument("--pane-id", required=True)
    sp.add_argument("--tab-id", required=True)
    sp.add_argument("--lease-seconds", type=int,
                    default=core.DEFAULT_LEASE_SECONDS)
    sp.add_argument("--herdr-bin")
    sp.set_defaults(func=cmd_worker_loop)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        args.func(args)
    except core.AgentBusError as exc:
        _fail(exc)
    except json.JSONDecodeError as exc:
        _fail(exc)
    except KeyboardInterrupt:
        sys.exit(130)
    return 0


if __name__ == "__main__":
    sys.exit(main())

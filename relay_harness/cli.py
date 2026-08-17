"""Operator-facing CLI for the foundation phase."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import AgentConfig, ProjectProfile, RepositoryConfig, RuntimeConfig
from .journal import StructuredJournal
from .model_policy import REQUIRED_MODEL, REQUIRED_REASONING
from .runtime import RuntimeLayout


def profile_path(project: str, root: Path) -> Path:
    return root / "projects" / f"{project}.json"


def load_profile(project: str, root: Path) -> ProjectProfile:
    return ProjectProfile.load(profile_path(project, root))


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    path = profile_path(args.project, root)
    if path.exists() and not args.force:
        raise SystemExit(f"profile already exists: {path} (use --force to replace)")
    profile = ProjectProfile(
        project_id=args.project,
        repository=RepositoryConfig(args.repository_path, args.branch),
        agent=AgentConfig(args.agent_command, REQUIRED_MODEL, REQUIRED_REASONING),
        runtime=RuntimeConfig(args.runtime_root),
    )
    profile.save(path)
    RuntimeLayout(profile).ensure()
    print(path)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    profile = load_profile(args.project, Path(args.root))
    layout = RuntimeLayout(profile)
    paths = layout.new_run()
    StructuredJournal(paths.logs / "journal.jsonl", {"project": profile.project_id, "run": paths.root.name}).append("run_created", model=profile.agent.model, reasoning=profile.agent.reasoning)
    print(paths.root.name)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    profile = load_profile(args.project, Path(args.root))
    layout = RuntimeLayout(profile)
    runs = []
    for manifest in sorted(layout.runs_root.glob("*/run.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        runs.append({"run_id": data.get("run_id"), "status": data.get("status"), "created_at": data.get("created_at")})
    print(json.dumps(runs, indent=2))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    profile = load_profile(args.project, Path(args.root))
    paths = RuntimeLayout(profile).run(args.run_id)
    target = RuntimeLayout(profile).mark_terminal(paths, args.reason)
    print(target)
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    profile = load_profile(args.project, Path(args.root))
    print(json.dumps(RuntimeLayout(profile).inspect_recovery(args.run_id), indent=2))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    return cmd_recover(args)


def cmd_doctor(args: argparse.Namespace) -> int:
    profile = load_profile(args.project, Path(args.root))
    profile.validate()
    print(json.dumps({"project": profile.project_id, "profile": "valid", "model": profile.agent.model, "reasoning": profile.agent.reasoning}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relayharness")
    parser.add_argument("--root", default=".relayharness", help="profile/runtime control root")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("project")
    init.add_argument("--repository-path", default=".")
    init.add_argument("--branch", default=None)
    init.add_argument("--runtime-root", default=".relayharness")
    init.add_argument("--agent-command", nargs="+", default=["codex", "exec"])
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)
    start = sub.add_parser("start")
    start.add_argument("project")
    start.set_defaults(func=cmd_start)
    status = sub.add_parser("status")
    status.add_argument("project")
    status.set_defaults(func=cmd_status)
    stop = sub.add_parser("stop")
    stop.add_argument("project")
    stop.add_argument("run_id")
    stop.add_argument("--reason", default="operator requested safe stop")
    stop.set_defaults(func=cmd_stop)
    for name, func in (("recover", cmd_recover), ("inspect", cmd_inspect)):
        command = sub.add_parser(name)
        command.add_argument("project")
        command.add_argument("run_id")
        command.set_defaults(func=func)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("project")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

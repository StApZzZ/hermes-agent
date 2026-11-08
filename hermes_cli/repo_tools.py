"""``hermes repos``: set Hermes up with the tools your own repositories already ship.

``scan`` reads repositories next to the current one (or the roots given) and prints a plan;
``apply`` carries it out, idempotently:

- an MCP server a repo ships → an ``mcp_servers`` entry (stdio from the repo's own venv or ``uv``;
  a local URL when a ``systemd --user`` unit already serves it over HTTP; disabled while the repo's
  ``.env`` is still missing);
- project skills (``.hermes/skills``, ``.agents/skills``, ``.claude/skills``) → the repo joins
  ``skills.trusted_project_dirs``, so they load in sessions started inside it;
- a skill pack elsewhere in a repo → ``skills.external_dirs``;
- console tools → a generated skill per repo under ``<HERMES_HOME>/repo-skills/`` (on
  ``skills.external_dirs``) that names each command with its absolute path.

Every path is this machine's, so nothing here travels with ``hermes sync`` (``mcp_servers`` and
``skills.*_dirs`` are machine-local keys; ``repo-skills/`` is outside its scope). Entries this
command created are recorded in ``repo-skills/manifest.json``: a re-run updates those and never
touches an entry you made yourself. Changes take effect in new sessions.
"""

from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.repo_tools_scan import RepoFinding, repos_under, scan_repo, user_service_targets


def repo_skills_dir() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "repo-skills"


def _manifest_path() -> Path:
    return repo_skills_dir() / "manifest.json"


def _load_manifest() -> dict:
    try:
        data = json.loads(_manifest_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"mcp": {}}
    return data if isinstance(data.get("mcp"), dict) else {"mcp": {}}


def is_hermes_checkout(repo: Path) -> bool:
    """A Hermes source tree: its bundled skills are already Hermes's own."""
    return (repo / "run_agent.py").is_file() and (repo / "hermes_cli").is_dir()


def default_roots() -> list[Path]:
    """The folder holding the current project (a git checkout, or a folder with a Python or Node
    manifest), else the current folder."""
    from agent.skill_utils import find_project_root
    from hermes_cli.repo_tools_scan import is_project
    cwd = Path.cwd().resolve()
    project = find_project_root(cwd) or (cwd if is_project(cwd) else None)
    return [project.parent if project else cwd]


KINDS = ("mcp", "skills", "tools")


def _projects(path: Path) -> list[Path]:
    """``path`` itself when it is a project, else the projects directly under it."""
    from hermes_cli.repo_tools_scan import is_project
    path = path.expanduser().resolve()
    return [path] if is_project(path) else repos_under(path)


def _keep_kinds(finding: RepoFinding, kinds: set[str]) -> RepoFinding:
    if "mcp" not in kinds:
        finding.mcp = []
    if "skills" not in kinds:
        finding.project_skills, finding.skill_packs = 0, {}
    if "tools" not in kinds:
        finding.commands = {}
    return finding


def scan(paths: list[Path], *, only=(), kinds=KINDS) -> list[RepoFinding]:
    """Findings for the projects at or under ``paths``; ``only`` keeps those folder names, ``kinds``
    keeps those of ``KINDS``."""
    from hermes_platform.resolver import locate_command
    uv, node = (locate_command(name).command for name in ("uv", "node"))
    uv, node = (uv[0] if uv else None), (node[0] if node else None)
    services, only, kinds = user_service_targets(), set(only), set(kinds)
    findings = []
    for path in paths:
        for repo in _projects(path):
            if is_hermes_checkout(repo) or (only and repo.name not in only):
                continue
            finding = _keep_kinds(scan_repo(repo, uv=uv, node=node, services=services), kinds)
            if not finding.empty:
                findings.append(finding)
    _dedupe_server_names(findings)
    return findings


def _dedupe_server_names(findings: list[RepoFinding]) -> None:
    """Two repos shipping a server of the same name (a fork beside its upstream): the later one is
    named after its folder instead."""
    from hermes_cli.repo_tools_scan import server_name
    taken: set[str] = set()
    for finding in findings:
        for server in finding.mcp:
            if server.name in taken:
                server.name = server_name(finding.repo.name)
            taken.add(server.name)


def _skill_name(repo: Path) -> str:
    from hermes_cli.repo_tools_scan import server_name
    return "repo-" + server_name(repo.name)


def cli_skill_text(finding: RepoFinding) -> str:
    """The generated skill for a repo's console tools."""
    names = ", ".join(finding.commands)
    summary = finding.summary or f"Tools from the {finding.repo.name} repository."
    description = f"{summary} Commands: {names}."
    lines = [
        "---",
        f"name: {_skill_name(finding.repo)}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
        "metadata:",
        "  hermes:",
        "    category: repo-tools",
        "---",
        "",
        f"# {finding.repo.name}",
        "",
        summary,
        "",
        "## Commands",
        "",
        "Run a command with `--help` first: options come from its own help, not from this page.",
        "",
    ]
    lines += [f"- `{name}`: `{' '.join(argv)}`" for name, argv in finding.commands.items()]
    lines += [
        "",
        "## Source",
        "",
        f"Repository: `{finding.repo}`. Read its README before changing anything there.",
        "",
        "Generated by `hermes repos apply`; run it again after the repository changes.",
        "",
    ]
    return "\n".join(lines)


def _add_unique(values, item: str) -> tuple[list[str], bool]:
    items = [str(v) for v in (values if isinstance(values, list) else [values] if values else [])]
    resolved = {str(Path(v).expanduser().resolve()) for v in items}
    if str(Path(item).resolve()) in resolved:
        return items, False
    return [*items, item], True


def _apply_mcp(config: dict, findings: list[RepoFinding], manifest: dict, report: list[str]) -> None:
    from hermes_cli.mcp_security import validate_mcp_server_entry
    servers = config.setdefault("mcp_servers", {})
    for finding in findings:
        for server in finding.mcp:
            if server.blocked:
                report.append(f"  skip  mcp {server.name}: {server.blocked}")
                continue
            entry = dict(server.config)
            if server.note.startswith("needs settings"):
                entry["enabled"] = False
            owned = manifest["mcp"].get(server.name) == str(finding.repo)
            if server.name in servers and not owned:
                report.append(f"  keep  mcp {server.name}: you already have one with this name")
                continue
            issues = validate_mcp_server_entry(server.name, entry)
            if issues:
                report.append(f"  skip  mcp {server.name}: {'; '.join(issues)}")
                continue
            changed = servers.get(server.name) != entry
            servers[server.name] = entry
            manifest["mcp"][server.name] = str(finding.repo)
            state = "off" if entry.get("enabled") is False else "on"
            report.append(f"  {'set ' if changed else 'same'}  mcp {server.name} ({state})"
                          + (f": {server.note}" if server.note else ""))


def _apply_skills(config: dict, findings: list[RepoFinding], report: list[str]) -> None:
    skills_cfg = config.setdefault("skills", {})
    for finding in findings:
        if finding.project_skills and not (finding.repo / ".git").exists():
            report.append(f"  skip  trust {finding.repo}: not a git checkout, so Hermes cannot tell it is the project")
        elif finding.project_skills:
            skills_cfg["trusted_project_dirs"], added = _add_unique(
                skills_cfg.get("trusted_project_dirs"), str(finding.repo))
            report.append(f"  {'set ' if added else 'same'}  trust {finding.repo} "
                          f"({finding.project_skills} project skills, loaded in sessions started there)")
        for pack, count in finding.skill_packs.items():
            skills_cfg["external_dirs"], added = _add_unique(skills_cfg.get("external_dirs"), str(pack))
            report.append(f"  {'set ' if added else 'same'}  skills {pack} ({count} skills, everywhere)")
        if finding.commands:
            target = repo_skills_dir() / finding.repo.name / "SKILL.md"
            text = cli_skill_text(finding)
            changed = not target.is_file() or target.read_text(encoding="utf-8-sig") != text
            if changed:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            report.append(f"  {'set ' if changed else 'same'}  skill {_skill_name(finding.repo)} "
                          f"({', '.join(finding.commands)})")
    if any(f.commands for f in findings):
        skills_cfg["external_dirs"], _ = _add_unique(skills_cfg.get("external_dirs"), str(repo_skills_dir()))


def apply(findings: list[RepoFinding]) -> list[str]:
    """Carry out the plan; returns one report line per item."""
    from hermes_cli.config import load_config, save_config
    from utils import atomic_write_text

    config, manifest, report = load_config(), _load_manifest(), []
    _apply_mcp(config, findings, manifest, report)
    _apply_skills(config, findings, report)
    save_config(config)
    _manifest_path().parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(_manifest_path(), json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True))
    return report


def _describe(finding: RepoFinding) -> list[str]:
    lines = [f"{finding.repo}" + (f" — {finding.summary[:90]}" if finding.summary else "")]
    for server in finding.mcp:
        how = server.config.get("url") or " ".join([server.config.get("command", ""), *server.config.get("args", [])])
        extra = server.blocked or server.note
        lines.append(f"  mcp    {server.name}: {how.strip() or '-'}" + (f"  [{extra}]" if extra else ""))
    if finding.project_skills:
        lines.append(f"  trust  {finding.project_skills} project skills")
    for pack, count in finding.skill_packs.items():
        lines.append(f"  skills {pack.relative_to(finding.repo) if pack != finding.repo else '.'}: {count}")
    if finding.commands:
        lines.append(f"  tools  {', '.join(finding.commands)} → skill {_skill_name(finding.repo)}")
    return lines


def _names(raw: str | None) -> list[str]:
    return [name.strip() for name in (raw or "").split(",") if name.strip()]


def cmd_repos(args) -> int:
    roots = [Path(r) for r in (args.roots or [])] or default_roots()
    kinds = _names(getattr(args, "kind", None)) or list(KINDS)
    unknown = sorted(set(kinds) - set(KINDS))
    if unknown:
        print(f"Unknown --kind {', '.join(unknown)}: choose from {', '.join(KINDS)}.")
        return 2
    findings = scan(roots, only=_names(getattr(args, "only", None)), kinds=kinds)
    if not findings:
        print(f"Nothing to set up in {', '.join(map(str, roots))}.")
        return 0
    if args.repos_action != "apply":
        for finding in findings:
            print("\n".join(_describe(finding)))
        print("\nApply with: hermes repos apply " + " ".join(map(str, roots))
              + "".join(f" --{opt} {getattr(args, opt)}" for opt in ("only", "kind") if getattr(args, opt, None)))
        return 0
    for line in apply(findings):
        print(line)
    print("\nNew sessions pick these up. Check an MCP server with `hermes mcp test NAME`;"
          " turn one on with `hermes config set mcp_servers.NAME.enabled true`.")
    return 0

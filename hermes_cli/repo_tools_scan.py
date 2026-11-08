"""What a source repository offers Hermes, read from its files alone: nothing is run or imported.

One ``RepoFinding`` per repository: MCP servers it ships (a console script named ``*mcp*``, a PEP 723
``server.py``, a Node package on the MCP SDK), project skill folders, skill packs elsewhere in the
tree, and other console tools. ``hermes_cli.repo_tools`` turns findings into a plan and applies it.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from agent.skill_utils import PROJECT_SKILLS_SUBDIRS

# Vendored or generated trees: a SKILL.md inside an installed package belongs to that package.
_SKIP_DIRS = frozenset({"node_modules", ".venv", "venv", "site-packages", "__pycache__", "dist", "build", ".git"})
_MCP_IMPORT = re.compile(r"^\s*(?:from\s+mcp[.\s]|import\s+mcp\b|from\s+fastmcp\b)|FastMCP\(", re.MULTILINE)
_LOCAL_MCP_URL = re.compile(r"http://(?:127\.0\.0\.1|localhost):\d+/mcp\b")
_SERVICE_NAME = re.compile(r"bot$")  # long-running bots are services, not tools


@dataclass
class McpServer:
    name: str
    config: dict  # an ``mcp_servers.<name>`` entry
    note: str = ""
    blocked: str = ""  # why it cannot be added yet (e.g. not built)


@dataclass
class RepoFinding:
    repo: Path
    summary: str = ""
    mcp: list[McpServer] = field(default_factory=list)
    project_skills: int = 0  # SKILL.md files in .hermes/skills, .agents/skills, .claude/skills
    skill_packs: dict[Path, int] = field(default_factory=dict)  # pack dir -> SKILL.md count
    commands: dict[str, list[str]] = field(default_factory=dict)  # console tool -> argv

    @property
    def empty(self) -> bool:
        return not (self.mcp or self.project_skills or self.skill_packs or self.commands)


def server_name(raw: str) -> str:
    """``maps-lead-mcp`` → ``maps-lead``, ``mcp-simpleone`` → ``simpleone``: the name an MCP entry gets."""
    name = re.sub(r"[^a-z0-9_-]+", "-", raw.lower()).strip("-")
    name = re.sub(r"^mcp[-_]|[-_]mcp$", "", name)
    return name or "mcp"


def _pyproject(repo: Path) -> dict:
    try:
        return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _package_json(repo: Path) -> dict:
    try:
        data = json.loads((repo / "package.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _readme_summary(repo: Path) -> str:
    for name in ("README.md", "readme.md", "README.rst", "README"):
        try:
            text = (repo / name).read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "!", "<", "[", "|", "```", "---")):
                line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)  # [text](link) -> text
                return re.sub(r"[*_`]", "", line)[:200]
    return ""


def _script_argv(repo: Path, script: str, uv: str | None) -> list[str] | None:
    venv_bin = repo / ".venv" / "bin" / script
    if venv_bin.is_file():
        return [str(venv_bin)]
    return [uv, "run", "--quiet", "--directory", str(repo), script] if uv else None


def _env_note(repo: Path) -> str:
    if (repo / ".env.example").is_file() and not (repo / ".env").is_file():
        return f"needs settings: copy {repo}/.env.example to .env and fill it, then enable it"
    return ""


def _stdio(argv: list[str], repo: Path) -> dict:
    return {"command": argv[0], "args": argv[1:], "cwd": str(repo)}


def _python_mcp(repo: Path, scripts: dict, uv: str | None) -> list[McpServer]:
    found = []
    for script in sorted(s for s in scripts if "mcp" in s.lower()):
        argv = _script_argv(repo, script, uv)
        if argv is None:
            found.append(McpServer(server_name(script), {}, blocked="no .venv and no uv to run it"))
        else:
            found.append(McpServer(server_name(script), _stdio(argv, repo), note=_env_note(repo)))
    return found


def _script_servers(repo: Path, uv: str | None, services: dict[Path, str]) -> list[McpServer]:
    """Single-file servers with inline (PEP 723) dependencies: ``uv run --script server.py``."""
    found = []
    for path in sorted(repo.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        if "# /// script" not in text or not _MCP_IMPORT.search(text):
            continue
        name = server_name(repo.name if path.stem in ("server", "main", "__main__") else path.stem)
        url = services.get(path.resolve()) and _LOCAL_MCP_URL.search(text)
        if url:  # already served over HTTP by a user service: a second stdio copy would race it
            found.append(McpServer(name, {"url": url.group(0)}, note=f"served by {services[path.resolve()]}"))
        elif uv is None:
            found.append(McpServer(name, {}, blocked="needs uv to run its inline dependencies"))
        else:
            locked = ["--locked"] if path.with_name(path.name + ".lock").is_file() else []
            argv = [uv, "run", "--quiet", *locked, "--script", str(path)]
            found.append(McpServer(name, _stdio(argv, repo), note=_env_note(repo)))
    return found


def _node_mcp(repo: Path, pkg: dict, node: str | None) -> list[McpServer]:
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    if "@modelcontextprotocol/sdk" not in deps:
        return []
    bin_field = pkg.get("bin")
    entry = next(iter(bin_field.values()), None) if isinstance(bin_field, dict) else bin_field or pkg.get("main")
    name = server_name(str(pkg.get("name") or repo.name).split("/")[-1])
    if not entry:
        return [McpServer(name, {}, blocked="package.json names no bin or main")]
    target = Path(os.path.normpath(repo / entry))  # the repo's path, not where a symlink leads
    if not target.is_file():
        return [McpServer(name, {}, blocked=f"not built: {entry} is missing (npm run build)")]
    if node is None:
        return [McpServer(name, {}, blocked="node is not installed")]
    return [McpServer(name, _stdio([node, str(target)], repo), note=_env_note(repo))]


def _console_commands(repo: Path, scripts: dict, pkg: dict, uv: str | None) -> dict[str, list[str]]:
    commands = {}
    for script in sorted(scripts):
        if "mcp" in script.lower() or _SERVICE_NAME.search(script):
            continue
        argv = _script_argv(repo, script, uv)
        if argv:
            commands[script] = argv
    bins = pkg.get("bin") if isinstance(pkg.get("bin"), dict) else {}
    deps = pkg.get("dependencies") or {}
    if "@modelcontextprotocol/sdk" not in deps:
        for name, rel in sorted(bins.items()):
            if (repo / rel).is_file() and not _SERVICE_NAME.search(name):
                commands[name] = [str((repo / rel).resolve())]
    return commands


def _skill_files(repo: Path):
    for path in repo.rglob("SKILL.md"):
        rel = path.relative_to(repo).parts
        if not any(part in _SKIP_DIRS for part in rel[:-1]):
            yield rel


def _skills(repo: Path) -> tuple[int, dict[Path, int]]:
    project_roots = tuple(Path(sub).parts for sub in PROJECT_SKILLS_SUBDIRS)
    project, packs = 0, {}
    for rel in _skill_files(repo):
        if any(rel[:len(root)] == root for root in project_roots):
            project += 1
        elif rel[0].startswith("."):
            continue  # another tool's private folder
        else:
            pack = repo if len(rel) == 1 else repo / rel[0]
            packs[pack] = packs.get(pack, 0) + 1
    return project, packs


def user_service_targets() -> dict[Path, str]:
    """Files a ``systemd --user`` unit serves over HTTP (``ExecStart=... FILE ... --http``) → unit name."""
    units = Path.home() / ".config" / "systemd" / "user"
    found = {}
    for unit in sorted(units.glob("*.service")) if units.is_dir() else ():
        try:
            text = unit.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("ExecStart=") and "--http" in line:
                for token in line.split("=", 1)[1].split():
                    if token.endswith(".py"):
                        found[Path(token.replace("%h", str(Path.home()))).expanduser().resolve()] = unit.name
    return found


def scan_repo(repo: Path, *, uv: str | None, node: str | None, services: dict[Path, str]) -> RepoFinding:
    pyproject, pkg = _pyproject(repo), _package_json(repo)
    scripts = (pyproject.get("project") or {}).get("scripts") or {}
    finding = RepoFinding(repo=repo, summary=str(pkg.get("description") or "") or _readme_summary(repo))
    finding.mcp = [*_python_mcp(repo, scripts, uv), *_script_servers(repo, uv, services), *_node_mcp(repo, pkg, node)]
    finding.project_skills, finding.skill_packs = _skills(repo)
    finding.commands = _console_commands(repo, scripts, pkg, uv)
    return finding


_PROJECT_MARKERS = (".git", "pyproject.toml", "package.json", "setup.py")


def repos_under(root: Path) -> list[Path]:
    """Projects directly under ``root``: git checkouts (a worktree's ``.git`` file counts) and
    folders with a Python or Node manifest."""
    if not root.is_dir():
        return []
    return [p for p in sorted(root.iterdir()) if p.is_dir() and not p.name.startswith(".") and is_project(p)]


def is_project(path: Path) -> bool:
    return any((path / marker).exists() for marker in _PROJECT_MARKERS)

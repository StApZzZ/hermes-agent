"""Behaviour of `hermes sync`: two real Hermes roots, the real agent protocol, no SSH.

The peer runs the same `serve_agent` the remote end runs over SSH, in-process against its own
root, and the reply goes through the same `parse_reply` the SSH peer uses.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from hermes_cli.home_sync import parse_reply, sync_host
from hermes_cli.home_sync_scope import KIND_ENTRIES, KIND_FILE, KIND_YAML, file_hash, kind_of, scan
from hermes_cli.home_sync_store import StaleError, SyncError, apply_ops, serve_agent

DELIM = "\n§\n"


class DirPeer:
    """The remote end, served from another root exactly as `hermes sync agent` serves it."""

    def __init__(self, root: Path, monkeypatch):
        self.root, self.monkeypatch = root, monkeypatch

    def call(self, request: dict) -> dict:
        out = io.StringIO()
        with self.monkeypatch.context() as m:
            m.setenv("HERMES_HOME", str(self.root))
            m.setattr(Path, "home", lambda: self.root.parent)
            serve_agent(self.root, io.StringIO(json.dumps(request)), out)
        return parse_reply("motd banner\n" + out.getvalue(), "", 0, "peer")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    local, remote = tmp_path / "local" / ".hermes", tmp_path / "remote" / ".hermes"
    local.mkdir(parents=True)
    remote.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "local")
    monkeypatch.setenv("HERMES_HOME", str(local))
    return local, remote, DirPeer(remote, monkeypatch)


def put(root: Path, rel: str, text: str, mtime: float | None = None) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def entries(root: Path, rel: str) -> list[str]:
    return [e.strip() for e in (root / rel).read_text(encoding="utf-8").split(DELIM) if e.strip()]


def config(root: Path, rel: str = "config.yaml") -> dict:
    import hermes_yaml
    return hermes_yaml.safe_load((root / rel).read_text(encoding="utf-8")) or {}


def sync(peer, **kw):
    return sync_host("peer", peer=peer, **kw)


def test_first_sync_unites_both_sides_and_keeps_machine_settings(roots):
    local, remote, peer = roots
    put(local, "SOUL.md", "You are Hermes.")
    put(local, "skills/mine/notes/SKILL.md", "---\nname: notes\n---\nTake notes.")
    put(local, "memories/MEMORY.md", "deploys on thursdays")
    put(local, "config.yaml", "model:\n  provider: claude-cli\nterminal:\n  cwd: /home/alex\n")
    put(local, "profiles/work/config.yaml", "model:\n  provider: codex-cli\n")
    put(local, "profiles/work/SOUL.md", "You are the work agent.")
    put(local, "profiles/work/memories/USER.md", "works at ACME")
    put(remote, "memories/MEMORY.md", "server is in Almaty")
    put(remote, "memories/USER.md", "name is Alex")
    put(remote, "config.yaml", "display:\n  skin: mono\nterminal:\n  cwd: /home/hermes\n")

    report = sync(peer)

    assert not report.conflicts
    for root in (local, remote):
        assert (root / "SOUL.md").read_text() == "You are Hermes."
        assert (root / "skills/mine/notes/SKILL.md").is_file()
        assert sorted(entries(root, "memories/MEMORY.md")) == ["deploys on thursdays", "server is in Almaty"]
        assert entries(root, "memories/USER.md") == ["name is Alex"]
        assert entries(root, "profiles/work/memories/USER.md") == ["works at ACME"]
        assert (root / "profiles/work/SOUL.md").read_text() == "You are the work agent."
        assert config(root)["model"]["provider"] == "claude-cli"
        assert config(root)["display"]["skin"] == "mono"
        assert config(root, "profiles/work/config.yaml")["model"]["provider"] == "codex-cli"
    assert config(local)["terminal"]["cwd"] == "/home/alex"
    assert config(remote)["terminal"]["cwd"] == "/home/hermes"
    assert sync(peer).changed is False


def test_one_sided_edits_and_deletions_follow(roots):
    local, remote, peer = roots
    put(local, "SOUL.md", "v1")
    put(local, "skills/tool/SKILL.md", "---\nname: tool\n---\n")
    sync(peer)

    put(local, "SOUL.md", "v2")
    (remote / "skills/tool/SKILL.md").unlink()
    report = sync(peer)

    assert (remote / "SOUL.md").read_text() == "v2"
    assert not (local / "skills/tool/SKILL.md").exists()
    assert report.deleted_local == ["skills/tool/SKILL.md"]


def test_an_edit_beats_a_deletion_on_the_other_side(roots):
    local, remote, peer = roots
    put(local, "SOUL.md", "v1")
    sync(peer)
    (local / "SOUL.md").unlink()
    put(remote, "SOUL.md", "v2")

    sync(peer)

    assert (local / "SOUL.md").read_text() == "v2"


def test_memory_edited_on_both_sides_merges_entry_by_entry(roots):
    local, remote, peer = roots
    put(local, "memories/MEMORY.md", DELIM.join(["x", "y"]))
    sync(peer)
    put(local, "memories/MEMORY.md", DELIM.join(["y", "z"]))  # removed x, added z
    put(remote, "memories/MEMORY.md", DELIM.join(["x", "y", "w"]))  # added w

    report = sync(peer)

    assert entries(local, "memories/MEMORY.md") == ["y", "z", "w"]
    assert entries(remote, "memories/MEMORY.md") == ["y", "z", "w"]
    assert report.merged == ["memories/MEMORY.md"] and not report.conflicts


def test_config_edited_on_both_sides_merges_keys_and_the_newer_side_wins_a_clash(roots):
    local, remote, peer = roots
    put(local, "config.yaml", "display:\n  skin: default\nagent:\n  max_turns: 50\n")
    sync(peer)
    put(local, "config.yaml", "display:\n  skin: ares\nagent:\n  max_turns: 90\n", mtime=1_000)
    put(remote, "config.yaml", "display:\n  skin: slate\nagent:\n  max_turns: 50\nmemory:\n  memory_enabled: false\n",
        mtime=2_000)

    report = sync(peer)

    for root in (local, remote):
        assert config(root)["display"]["skin"] == "slate"
        assert config(root)["agent"]["max_turns"] == 90
        assert config(root)["memory"]["memory_enabled"] is False
    assert len(report.conflicts) == 1 and "display.skin" in report.conflicts[0]


def test_config_comments_survive_a_merge(roots):
    local, remote, peer = roots
    put(local, "config.yaml", "# my notes\ndisplay:\n  skin: default  # keep it calm\n")
    sync(peer)
    put(remote, "config.yaml", "display:\n  skin: default\nagent:\n  max_turns: 70\n")

    sync(peer)

    text = (local / "config.yaml").read_text()
    assert "# my notes" in text and "# keep it calm" in text and "max_turns: 70" in text


def test_a_file_edited_on_both_sides_keeps_the_newer_and_saves_the_other(roots):
    local, remote, peer = roots
    put(local, "SOUL.md", "base")
    sync(peer)
    put(local, "SOUL.md", "local edit", mtime=1_000)
    put(remote, "SOUL.md", "remote edit", mtime=2_000)

    report = sync(peer)

    assert (local / "SOUL.md").read_text() == "remote edit"
    kept = Path(report.conflict_dir) / "local" / "SOUL.md"
    assert kept.read_text() == "local edit"


def test_dry_run_changes_nothing(roots):
    local, remote, peer = roots
    put(local, "SOUL.md", "hello")

    report = sync(peer, dry_run=True)

    assert report.pushed == ["SOUL.md"]
    assert not (remote / "SOUL.md").exists()
    assert not (local / "sync").joinpath("peer", "state.json").exists()


@pytest.mark.parametrize("rel", [
    ".env", "auth.json", "state.db", "sessions/x.json", "logs/agent.log", "profiles/work/.env",
    "skills/../.env", "skills/.usage.json", "skills/.bundled_manifest", "/etc/passwd",
    "profiles/../config.yaml", "spaces/chat_selections.json",
])
def test_secrets_runtime_state_and_escapes_are_outside_the_scope(rel):
    assert kind_of(rel) is None


@pytest.mark.parametrize("rel,kind", [
    ("config.yaml", KIND_YAML), ("profiles/work/config.yaml", KIND_YAML), ("spaces.yaml", KIND_YAML),
    ("memories/USER.md", KIND_ENTRIES), ("spaces/SHARED.md", KIND_ENTRIES),
    ("spaces/work/SPACE.md", KIND_ENTRIES), ("SOUL.md", KIND_FILE), ("skills/dev/x/SKILL.md", KIND_FILE),
    ("skills/.hub/lock.json", KIND_FILE),
])
def test_user_data_is_inside_the_scope(rel, kind):
    assert kind_of(rel) == kind


def test_the_peer_refuses_to_write_outside_the_scope(roots):
    _local, remote, _peer = roots
    request = {"op": "apply", "ops": [{"action": "write", "path": ".env", "expect": None, "data": "eA=="}]}
    out = io.StringIO()

    assert serve_agent(remote, io.StringIO(json.dumps(request)), out) == 1
    assert not (remote / ".env").exists()
    with pytest.raises(SyncError):
        parse_reply(out.getvalue(), "", 1, "peer")


def test_a_target_that_moved_since_planning_is_not_overwritten(roots):
    local, _remote, _peer = roots
    put(local, "SOUL.md", "changed meanwhile")

    with pytest.raises(StaleError):
        apply_ops(local, [{"action": "write", "path": "SOUL.md", "expect": "0" * 64, "data": "eA=="}])
    assert (local / "SOUL.md").read_text() == "changed meanwhile"


def test_untouched_bundled_skills_stay_home_but_edited_ones_travel(roots):
    local, _remote, _peer = roots
    from tools.skills_sync import dir_hash
    skill = local / "skills/research/arxiv"
    put(local, "skills/research/arxiv/SKILL.md", "---\nname: arxiv\n---\nSearch arXiv.")
    put(local, "skills/.bundled_manifest", f"arxiv:{dir_hash(skill)}\n")

    assert "skills/research/arxiv/SKILL.md" not in scan(local)
    put(local, "skills/research/arxiv/SKILL.md", "---\nname: arxiv\n---\nSearch arXiv, my way.")
    assert "skills/research/arxiv/SKILL.md" in scan(local)


def test_a_profile_deleted_on_the_peer_is_not_brought_back(roots):
    from hermes_constants import profile_tombstone_path
    local, remote, peer = roots
    put(local, "profiles/work/config.yaml", "model:\n  provider: codex-cli\n")
    put(remote, "skills/other/SKILL.md", "---\nname: other\n---\n")
    tombstone = profile_tombstone_path(remote / "profiles" / "work")
    tombstone.parent.mkdir(parents=True, exist_ok=True)
    tombstone.write_text("")

    with pytest.raises(SyncError, match="deleted"):
        sync(peer)
    assert not (remote / "profiles/work/config.yaml").exists()
    assert not (local / "skills/other/SKILL.md").exists()  # nothing half-applied


def test_a_missing_reply_names_the_fix():
    with pytest.raises(SyncError, match="hermes update"):
        parse_reply("", "hermes: invalid choice: 'sync'", 2, "hermes-dev")


def test_scan_hashes_match_file_contents(roots):
    local, _remote, _peer = roots
    path = put(local, "SOUL.md", "x")
    assert scan(local)["SOUL.md"]["hash"] == file_hash(path)

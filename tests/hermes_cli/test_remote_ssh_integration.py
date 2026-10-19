"""Real OpenSSH/tmux in a disposable container; agent/ACP payloads are fixtures.

Run explicitly with the canonical runner and -m integration. No user's SSH
hosts, known_hosts or credentials are used. The daemon binds only loopback.
"""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid

import pytest

from hermes_cli.subcommands import remote

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def checked(argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True, timeout=180, **kwargs).stdout


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the disposable SSH server")
    probe = subprocess.run(["docker", "info"], capture_output=True, timeout=20)
    if probe.returncode:
        pytest.skip("Docker daemon is unavailable")
    work = tmp_path_factory.mktemp("remote-ssh")
    image = "hermes-remote-test:" + uuid.uuid4().hex[:12]
    name = "hermes-remote-" + uuid.uuid4().hex[:12]
    key = work / "identity"
    checked(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key)])
    checked(["docker", "build", "-t", image, str(Path(__file__).parent / "remote_ssh")])
    started = False
    try:
        checked(["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::22", image])
        started = True
        checked(["docker", "cp", str(key) + ".pub", name + ":/home/dev/.ssh/authorized_keys"])
        checked(["docker", "exec", name, "sh", "-c",
                 "chown dev:dev /home/dev/.ssh/authorized_keys && chmod 600 /home/dev/.ssh/authorized_keys"])
        port = checked(["docker", "port", name, "22/tcp"]).decode().strip().rsplit(":", 1)[1]
        # Read the key from the container we started, not the network. SSH must
        # verify this exact host key; no StrictHostKeyChecking=no/accept-new.
        hostkey = checked(["docker", "exec", name, "cat", "/etc/ssh/ssh_host_ed25519_key.pub"]).decode().split()
        known = work / "known_hosts"
        known.write_text(f"[127.0.0.1]:{port} {hostkey[0]} {hostkey[1]}\n", encoding="utf-8")
        config = work / "config"
        config.write_text(f'''Host devbox
  HostName 127.0.0.1
  User dev
  Port {port}
  IdentityFile "{key.as_posix()}"
  UserKnownHostsFile "{known.as_posix()}"
  IdentitiesOnly yes
  StrictHostKeyChecking yes
  BatchMode yes
''', encoding="utf-8")
        yield config
    finally:
        if started:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)
        subprocess.run(["docker", "image", "rm", image], capture_output=True, timeout=30, check=False)


def ssh(server, command, check=True):
    return subprocess.run(remote._ssh_argv("devbox", command, batch=True, ssh_config=server),
                          capture_output=True, check=check, timeout=20)


def cli(server, action, workspace):
    return [sys.executable, str(Path(remote.__file__).resolve()), action, "devbox",
            "--workspace", workspace, "--ssh-config", str(server)]


def test_acp_real_ssh_roundtrip_eof_and_exit(server):
    workspace = "/home/dev/projects/ACP ' space; $(touch /tmp/injected)"
    ssh(server, "mkdir -p -- " + shlex.quote(workspace))
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}},
                {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": workspace}},
                {"jsonrpc": "2.0", "id": 3, "method": "fixture/echo", "params": {"text": "Привет" * 10000}}]
    payload = b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in requests)
    result = subprocess.run(cli(server, "acp", workspace), input=payload, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 3
    assert responses[0]["result"]["protocolVersion"] == 1
    assert responses[1]["result"]["cwd"] == workspace
    assert responses[2]["result"] == requests[2]["params"]
    assert b"ACP fixture diagnostic" in result.stderr
    ssh(server, "test ! -e /tmp/injected && test -f " + shlex.quote(workspace + "/.acp-closed"))
    payload = b'{"jsonrpc":"2.0","id":9,"method":"fixture/exit"}\n'
    exited = subprocess.run(cli(server, "acp", workspace), input=payload, capture_output=True, timeout=30)
    assert exited.returncode == 19
    assert exited.stdout == b""


def test_doctor_real_tools_missing_workspace_and_tools(server):
    good = subprocess.run(cli(server, "doctor", "/home/dev/projects"), capture_output=True, timeout=30)
    assert good.returncode == 0, good.stdout + good.stderr
    assert b"SSH: OK" in good.stdout and b"Cgroup limits" in good.stdout
    missing = subprocess.run(cli(server, "doctor", "/does-not-exist"), capture_output=True, timeout=30)
    assert missing.returncode == 1 and b"Workspace missing" in missing.stdout
    # A private PATH hides the development tools without mutating them.
    from hermes_cli.subcommands.remote_doctor import doctor_script
    ssh(server, 'mkdir -p /home/dev/doctor-bin; for tool in hostname awk df cat; do '
                'ln -sf "$(command -v "$tool")" "/home/dev/doctor-bin/$tool"; done')
    probe = doctor_script("/home/dev/projects").replace('export PATH="$HOME/.local/bin:$PATH"',
                                                       'export PATH=/home/dev/doctor-bin')
    result = ssh(server, probe, check=False)
    assert result.returncode == 1 and b"Claude Code missing" in result.stdout


def test_ssh_failure_has_no_protocol_stdout(server, tmp_path):
    bad = tmp_path / "bad-config"
    bad.write_text('Host devbox\n HostName 127.0.0.1\n Port 1\n BatchMode yes\n', encoding="utf-8")
    argv = cli(bad, "acp", "/home/dev/projects")
    result = subprocess.run(argv, input=b"", capture_output=True, timeout=20)
    assert result.returncode == 255
    assert result.stdout == b"" and result.stderr


def wait_remote_file(server, path):
    # Each SSH roundtrip supplies the polling interval; no fixed sleep or
    # assumption about how fast the container/agent process starts.
    for _ in range(60):
        if ssh(server, "test -f " + shlex.quote(path), check=False).returncode == 0:
            return
    pytest.fail(f"Remote process did not create {path}")


def test_tmux_survives_disconnect_reconnect_and_project_switch(server, tmp_path):
    processes = []
    logs = []
    env = dict(os.environ, TERM="xterm-256color")
    workspaces = ["/home/dev/projects/one ' quoted", "/home/dev/projects/two"]
    try:
        for index, workspace in enumerate(workspaces):
            ssh(server, "mkdir -p -- " + shlex.quote(workspace))
            log = (tmp_path / f"terminal-{index}.log").open("wb")
            logs.append(log)
            process = subprocess.Popen(cli(server, "claude", workspace), stdin=subprocess.PIPE,
                                       stdout=log, stderr=subprocess.STDOUT, env=env)
            processes.append(process)
            wait_remote_file(server, workspace + "/.ready")
            pid = ssh(server, "cat " + shlex.quote(workspace + "/.ready")).stdout.strip()
            process.stdin.write(b"remote edit\n")
            process.stdin.flush()
            wait_remote_file(server, workspace + "/edited-by-agent.txt")
            process.stdin.write(b"\x03")
            process.stdin.flush()
            wait_remote_file(server, workspace + "/.interrupted")
            name = remote._session_name("devbox", workspace, None, "claude")
            ssh(server, f"tmux -L hermes-remote resize-window -t ={name} -x 120 -y 40")
            wait_remote_file(server, workspace + "/.resized")
            process.terminate()
            process.wait(timeout=10)
            assert ssh(server, "kill -0 " + pid.decode()).returncode == 0
            reconnected = subprocess.Popen(cli(server, "claude", workspace), stdin=subprocess.PIPE,
                                          stdout=log, stderr=subprocess.STDOUT, env=env)
            processes.append(reconnected)
            # Prove the reconnected client reaches the existing process.
            reconnected.stdin.write(b"after reconnect\n")
            reconnected.stdin.flush()
            for _ in range(60):
                text = ssh(server, "cat " + shlex.quote(workspace + "/edited-by-agent.txt")).stdout
                if b"after reconnect" in text:
                    break
            assert b"after reconnect" in text
            assert ssh(server, "cat " + shlex.quote(workspace + "/.ready")).stdout.strip() == pid
        sessions = ssh(server, "tmux -L hermes-remote list-sessions -F '#S'").stdout.splitlines()
        assert len(sessions) == 2
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        for log in logs:
            log.close()
        ssh(server, "tmux -L hermes-remote kill-server", check=False)

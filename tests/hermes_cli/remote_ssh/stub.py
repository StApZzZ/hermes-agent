"""Transport test double; deliberately does not call an LLM or import Hermes."""

import json
import os
from pathlib import Path
import signal
import sys


if sys.argv[1] == "claude":
    Path(".ready").write_text(str(os.getpid()), encoding="utf-8")
    signal.signal(signal.SIGINT, lambda *args: Path(".interrupted").touch())
    signal.signal(signal.SIGWINCH, lambda *args: Path(".resized").touch())
    # A real agent owns its history; the fixture records enough to prove that
    # the same process and working directory survive an SSH disconnection.
    for line in sys.stdin:
        if line.strip() == "quit":
            break
        Path("edited-by-agent.txt").write_text(line, encoding="utf-8")
    sys.exit(0)

if "--check" in sys.argv:
    print("ACP transport fixture OK (not the real Hermes runtime)")
    sys.exit(0)

print("ACP fixture diagnostic", file=sys.stderr, flush=True)
try:
    for line in sys.stdin.buffer:
        request = json.loads(line)
        method = request["method"]
        if "id" not in request:
            continue
        if method == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {},
                      "agentInfo": {"name": "transport-fixture", "version": "1"}}
        elif method == "session/new":
            result = {"sessionId": "fixture-session", "cwd": os.getcwd()}
        elif method == "fixture/exit":
            sys.exit(19)
        else:
            result = request.get("params", {})
        payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}, ensure_ascii=False)
        sys.stdout.buffer.write(payload.encode() + b"\n")
        sys.stdout.buffer.flush()
finally:
    Path(".acp-closed").touch()

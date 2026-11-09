"""Offline executable fixture for official CLI wire formats; never calls a model."""

import base64
import json
import os
from pathlib import Path
import sys
import threading

engine, mode = sys.argv[1:3]
prompt = sys.stdin.read()
capture = Path(os.environ["CLI_FIXTURE_CAPTURE"])
argv = sys.argv[3:]
stream_json = "--input-format" in argv and argv[argv.index("--input-format") + 1] == "stream-json"
images = [base64.b64encode(Path(arg.split("=", 1)[1]).read_bytes()).decode() for arg in argv if arg.startswith("--image=")]
if stream_json:
    content = json.loads(prompt)["message"]["content"]
    prompt = "".join(block["text"] for block in content if block["type"] == "text")
    images = [block["source"]["data"] for block in content if block["type"] == "image"]
system = (Path(argv[argv.index("--system-prompt-file") + 1]).read_text(encoding="utf-8")
          if "--system-prompt-file" in argv else None)
capture.write_text(json.dumps({"argv":argv, "prompt":prompt, "system":system, "cwd":os.getcwd(), "images":images,
                               "env":{key:os.environ.get(key) for key in (
                                   "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "NO_BROWSER")}}))
if mode == "timeout":
    threading.Event().wait()
if mode == "exit":
    print("secret-device-code-must-not-escape", file=sys.stderr)
    sys.exit(19)
if mode == "malformed":
    print("unexpected shell banner")
    sys.exit(0)
if mode == "failed":
    print("secret-device-code-must-not-escape", file=sys.stderr)
    print(json.dumps({"type":"turn.failed", "error":{"message":"You've hit your usage limit."}}) if engine=="codex" else
          json.dumps({"is_error":True, "result":"You've hit your usage limit."}) if engine=="claude" else
          json.dumps({"error":{"type":"Error", "message":"You've hit your usage limit."}}))
    # Real CLIs exit non-zero on a failed turn and still print the structured error.
    sys.exit(1)
if mode == "tool":
    result = '<tool_call>' + json.dumps({"id":"fixture_call","type":"function",
        "function":{"name":"terminal","arguments":json.dumps({"command":"pwd"})}}) + '</tool_call>'
else:
    result = "Привет from " + engine
if engine == "codex":
    print(json.dumps({"type":"item.completed", "item":{"type":"agent_message", "text":result}}))
    print(json.dumps({"type":"turn.completed", "usage":{"input_tokens":12,"output_tokens":3,"cached_input_tokens":4}}))
elif stream_json:
    print(json.dumps({"type":"system", "subtype":"init"}))
    print(json.dumps({"type":"result", "subtype":"success", "is_error":False, "result":result,
                      "usage":{"input_tokens":12,"output_tokens":3}}))
else:
    print(json.dumps({"result" if engine=="claude" else "response":result,
                      "usage":{"input_tokens":12,"output_tokens":3}}))

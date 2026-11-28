"""Exercise Pi's actual slash command protocol against a controlled Titan endpoint."""
import json
import os
import shutil
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


EXTENSION = Path(__file__).resolve().parents[1] / "tools/pi_extension/index.ts"


@pytest.mark.skipif(not shutil.which("pi"), reason="Pi CLI is required for the native RPC test")
def test_save_requires_a_valid_response_and_preserves_pi_session(tmp_path):
    home = tmp_path / "titan"
    captured = []
    agent_name = "pi"

    class Runtime(BaseHTTPRequestHandler):
        def reply(self, status, data):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_GET(self):
            assert self.path == "/api/runtime"
            self.reply(200, {"agent_name": agent_name, "titan_home": str(home)})

        def do_POST(self):
            assert self.path == "/api/trace"
            captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if len(captured) == 1:
                self.reply(503, {"detail": "fixture unavailable"})
            else:
                self.reply(200, {"stored": False, "recap": "Trace captured. Memory extraction is pending retry."})

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session_id = str(uuid.uuid4())
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "SystemRoot") if key in os.environ}
    env.update(PI_CODING_AGENT_DIR=str(tmp_path / "pi"), PI_OFFLINE="1", TITAN_HOME=str(home),
               TITAN_PI_API_URL=f"http://127.0.0.1:{server.server_port}")
    command = ["pi", "--mode", "rpc", "--offline", "--no-approve", "--no-mcp", "--no-extensions",
               "--no-context-files", "--session-id", session_id, "-e", str(EXTENSION)]
    try:
        for expected in ("fixture unavailable", "pending retry", "did not identify this Pi workspace"):
            if len(captured) == 2:
                agent_name = "other"
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, env=env, cwd=tmp_path) as process:
                # Keep stdin open while the host activates extensions and dispatches the request.
                deadline = threading.Timer(20, process.kill)
                deadline.start()
                try:
                    process.stdin.write(json.dumps({"id": "save", "type": "prompt", "message": "/titan-save sample"}) + "\n")
                    process.stdin.flush()
                    notices = []
                    for line in process.stdout:
                        event = json.loads(line)
                        if event.get("method") == "notify":
                            notices.append(event["message"])
                        if event.get("id") == "save":
                            assert event["success"]
                            break
                    assert expected in "\n".join(notices), notices
                finally:
                    deadline.cancel()
                    process.terminate()
        assert [packet["session_id"] for packet in captured] == [session_id, session_id]
        assert all(packet["goal"] == "sample" and packet["outcome"] == "" for packet in captured)
        assert [p.stem for p in (home / "traces").glob("*.jsonl")] == [session_id]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

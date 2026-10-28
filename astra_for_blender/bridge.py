import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
from collections import deque

SYSTEM_SUFFIX = """
You are being invoked from Astra for Blender. Use the configured Blender MCP server for Blender requests. Inspect before changing. Do not access unrelated files, shell commands, or network resources unless explicitly requested. Return a concise summary.
"""

BUILD_VERSION = "0.12.2"
BLENDER_MCP_SERVER_NAME = "blender"


def candidate_codex_paths():
    out = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(os.path.join(local, "Programs", "OpenAI", "Codex", "bin", "codex.exe"))
    home = os.path.expanduser("~")
    out.append(os.path.join(home, "AppData", "Local", "Programs", "OpenAI", "Codex", "bin", "codex.exe"))
    return out


def resolve_command(command):
    command = os.path.expandvars(os.path.expanduser((command or "codex").strip()))
    if os.path.isabs(command) and os.path.isfile(command):
        return os.path.normpath(command)
    found = shutil.which(command)
    if found:
        return os.path.normpath(found)
    for path in candidate_codex_paths():
        if os.path.isfile(path):
            return os.path.normpath(path)
    return command


def normalize_workspace(workspace):
    return os.path.normpath(os.path.abspath(os.path.expandvars(os.path.expanduser((workspace or "").strip()))))


def validate_workspace(workspace):
    path = normalize_workspace(workspace)
    if not (workspace or "").strip():
        return False, path, "Workspace is empty."
    if not os.path.isdir(path):
        return False, path, f"Workspace does not exist: {path}"
    drive = os.path.splitdrive(path)[0]
    drive_root = os.path.abspath(drive + os.sep) if drive else os.path.abspath(os.sep)
    home = os.path.normcase(os.path.abspath(os.path.expanduser("~")))
    if os.path.normcase(path) in {os.path.normcase(drive_root), home}:
        return False, path, "Choose a dedicated subfolder, not a drive root or your whole user profile."
    return True, path, "OK"


def creation_flags():
    flags = 0
    if os.name == "nt":
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def codex_version(command, workspace):
    ok, workspace, reason = validate_workspace(workspace)
    if not ok:
        raise ValueError(reason)
    exe = resolve_command(command)
    proc = subprocess.run(
        [exe, "--version"],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=creation_flags(),
    )
    return exe, workspace, (proc.stdout or proc.stderr).strip(), proc.returncode


def blender_elicitation_decision(params, approve_blender_mcp):
    """Return (result, note) for a Codex mcpServer/elicitation/request.

    We only auto-accept the narrow approval shape used for MCP tool-call
    confirmation: server == 'blender', form mode, and an empty form schema.
    Any request that actually asks the user to supply fields is declined rather
    than fabricating answers. This keeps the checkbox scoped to approvals, not
    arbitrary elicitation/user-input flows.
    """
    params = params or {}
    server_name = params.get("serverName") or params.get("server_name") or ""
    mode = params.get("mode") or "form"
    schema = params.get("requestedSchema") or params.get("requested_schema") or {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    required = schema.get("required") if isinstance(schema, dict) else None
    properties = properties if isinstance(properties, dict) else {}
    required = required if isinstance(required, list) else []

    if not approve_blender_mcp:
        return {"action": "decline", "content": None, "_meta": None}, "Blender MCP approval declined (auto-approval disabled)."
    if server_name != BLENDER_MCP_SERVER_NAME:
        return {"action": "decline", "content": None, "_meta": None}, f"Approval declined for unrelated MCP server: {server_name or 'unknown'}."
    if mode != "form":
        return {"action": "decline", "content": None, "_meta": None}, f"Blender MCP request declined because mode '{mode}' needs explicit user handling."
    if properties or required:
        return {"action": "decline", "content": None, "_meta": None}, "Blender MCP request declined because it asks for user-provided form data."
    return {"action": "accept", "content": {}, "_meta": None}, "Blender MCP approval accepted for this tool call."


class AppServerClient:
    """Persistent JSONL client for `codex app-server` with Blender-only MCP approval handling."""

    def __init__(self, command, workspace, model, approve_blender_mcp=True, extra_args=""):
        ok, self.workspace, reason = validate_workspace(workspace)
        if not ok:
            raise ValueError(reason)
        self.exe = resolve_command(command)
        self.model = (model or "").strip()
        self.approve_blender_mcp = bool(approve_blender_mcp)
        self.extra_args = extra_args or ""
        self.proc = None
        self.thread_id = None
        self.turn_id = None
        self._id = 0
        self._responses = {}
        self._events = queue.Queue()
        self._approval_notes = queue.Queue()
        self._lock = threading.Lock()
        self._reader = None
        self._stderr_reader = None
        self._stderr = deque(maxlen=80)
        self._start()

    def _start(self):
        cmd = [self.exe, "app-server"]
        if self.extra_args:
            cmd += shlex.split(self.extra_args, posix=(os.name != "nt"))
        self.proc = subprocess.Popen(
            cmd,
            cwd=self.workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags(),
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True, name="astra-codex-reader")
        self._stderr_reader = threading.Thread(target=self._stderr_loop, daemon=True, name="astra-codex-stderr")
        self._reader.start()
        self._stderr_reader.start()

        self.request(
            "initialize",
            {
                "clientInfo": {"name": "astra_for_blender", "title": "Astra for Blender", "version": BUILD_VERSION},
                "capabilities": {"experimentalApi": True},
            },
            20,
        )
        self.notify("initialized")

        # Do not inject per-thread `config` here. Astra already relies on the
        # user's globally configured Blender MCP server, and recent Codex
        # versions have had regressions around per-thread config overlays.
        # Keep approval routing with the host so we can answer the Blender MCP
        # elicitation request explicitly instead of hoping a reviewer infers it.
        params = {
            "cwd": self.workspace,
            "ephemeral": False,
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "developerInstructions": SYSTEM_SUFFIX,
        }
        if self.model:
            params["model"] = self.model
        started = self.request("thread/start", params, 30)
        self.thread_id = (started.get("thread") or {}).get("id")
        if not self.thread_id:
            raise RuntimeError("Codex app-server did not return a thread id.")

    def _send(self, obj):
        if not self.proc or self.proc.poll() is not None:
            raise RuntimeError("Codex app-server is not running.")
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"
        with self._lock:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()

    def _send_server_result(self, request_id, result):
        self._send({"id": request_id, "result": result})

    def _send_server_error(self, request_id, message):
        self._send({"id": request_id, "error": {"code": -32601, "message": message}})

    def request(self, method, params=None, timeout=30):
        self._id += 1
        rid = self._id
        response_queue = queue.Queue(maxsize=1)
        self._responses[rid] = response_queue
        obj = {"method": method, "id": rid}
        if params is not None:
            obj["params"] = params
        self._send(obj)
        try:
            msg = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            detail = self.stderr_text()
            suffix = f"\nCodex stderr:\n{detail}" if detail else ""
            raise TimeoutError(f"Codex app-server timed out waiting for {method}.{suffix}") from exc
        finally:
            self._responses.pop(rid, None)
        if "error" in msg:
            err = msg["error"]
            raise RuntimeError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        return msg.get("result") or {}

    def notify(self, method, params=None):
        obj = {"method": method}
        if params is not None:
            obj["params"] = params
        self._send(obj)

    def _stderr_loop(self):
        try:
            for line in self.proc.stderr:
                line = line.rstrip()
                if line:
                    self._stderr.append(line)
        except Exception:
            pass

    def stderr_text(self):
        return "\n".join(self._stderr)[-6000:]

    def _handle_server_request(self, msg):
        request_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}
        if request_id is None:
            return

        if method == "mcpServer/elicitation/request":
            result, note = blender_elicitation_decision(params, self.approve_blender_mcp)
            self._approval_notes.put(note)
            self._send_server_result(request_id, result)
            return

        # Astra v0.8 deliberately does not silently grant shell, file-system,
        # extra-permission, or arbitrary user-input requests. Those are not
        # Blender MCP approvals and should remain blocked unless we add a UI
        # confirmation flow for them later.
        self._approval_notes.put(f"Blocked unsupported approval request: {method}")
        self._send_server_error(request_id, f"Astra only handles Blender MCP elicitation approvals; unsupported request: {method}")

    def _read_loop(self):
        try:
            for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                rid = msg.get("id")
                method = msg.get("method")
                if rid is not None and isinstance(method, str):
                    # Server-initiated JSON-RPC request. This *must* be answered
                    # by the app-server client; treating it as a notification is
                    # what caused the v0.5/v0.6 MCP approval failure.
                    try:
                        self._handle_server_request(msg)
                    except Exception as exc:
                        try:
                            self._send_server_error(rid, str(exc))
                        except Exception:
                            pass
                    continue
                if rid is not None and "method" not in msg:
                    response_queue = self._responses.get(rid)
                    if response_queue:
                        try:
                            response_queue.put_nowait(msg)
                        except queue.Full:
                            pass
                else:
                    self._events.put(msg)
        finally:
            self._events.put({"method": "__eof__"})

    def drain_approval_notes(self):
        notes = []
        while True:
            try:
                notes.append(self._approval_notes.get_nowait())
            except queue.Empty:
                return notes

    def run_turn(self, prompt, stop_event=None):
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                break
        self.drain_approval_notes()

        result = self.request(
            "turn/start",
            {"threadId": self.thread_id, "input": [{"type": "text", "text": prompt, "textElements": []}]},
            30,
        )
        self.turn_id = (result.get("turn") or {}).get("id")
        chunks = []
        final = ""

        while True:
            if stop_event and stop_event.is_set():
                self.interrupt()
                return {"stopped": True, "message": "", "approvals": self.drain_approval_notes()}
            try:
                msg = self._events.get(timeout=0.2)
            except queue.Empty:
                if self.proc.poll() is not None:
                    detail = self.stderr_text()
                    raise RuntimeError("Codex app-server exited unexpectedly." + (f"\n{detail}" if detail else ""))
                continue

            method = msg.get("method", "")
            params = msg.get("params") or {}
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if delta:
                    chunks.append(delta)
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("text"):
                    final = item["text"]
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if not self.turn_id or turn.get("id") == self.turn_id:
                    err = turn.get("error")
                    if err:
                        raise RuntimeError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
                    return {
                        "stopped": False,
                        "message": final or "".join(chunks).strip() or "Done.",
                        "approvals": self.drain_approval_notes(),
                    }
            elif method == "__eof__":
                detail = self.stderr_text()
                raise RuntimeError("Codex app-server connection closed." + (f"\n{detail}" if detail else ""))

    def interrupt(self):
        if self.thread_id and self.turn_id:
            try:
                self.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}, 5)
            except Exception:
                pass

    def close(self, force=False):
        proc = self.proc
        if not proc:
            return

        # Forced recovery must never wait on a possibly blocked stdin pipe.
        # On Windows kill the complete Codex/MCP process tree first.
        if force and proc.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        timeout=3,
                        check=False,
                    )
                else:
                    proc.kill()
                    proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self.proc = None
            return

        try:
            proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except Exception:
                pass
        self.proc = None


_client = None
_client_key = None
_client_lock = threading.Lock()


def get_client(command, workspace, model, approve_blender_mcp, extra_args):
    global _client, _client_key
    key = (
        resolve_command(command),
        normalize_workspace(workspace),
        (model or "").strip(),
        bool(approve_blender_mcp),
        extra_args or "",
    )
    with _client_lock:
        if _client is None or _client_key != key or not _client.proc or _client.proc.poll() is not None:
            if _client:
                try:
                    _client.close()
                except Exception:
                    pass
            _client = AppServerClient(command, workspace, model, approve_blender_mcp, extra_args)
            _client_key = key
        return _client


def stop_client_turn():
    with _client_lock:
        client = _client
    if client:
        client.interrupt()


def close_client():
    global _client, _client_key
    with _client_lock:
        client = _client
        _client = None
        _client_key = None
    if client:
        try:
            client.close()
        except Exception:
            pass


def force_close_client():
    """Drop the persistent app-server and forcibly end its process tree if needed."""
    global _client, _client_key
    with _client_lock:
        client = _client
        _client = None
        _client_key = None
    if client:
        try:
            client.close(force=True)
        except Exception:
            pass

"""A deliberately misbehaving stdio MCP server for deterministic tests.

Speaks raw newline-delimited JSON-RPC (no SDK) so tests control exactly what
goes over the wire. Each tool exercises one edge case; see TOOLS below.

Startup modes (first CLI argument):
  normal          behave
  crash-on-start  exit(7) before reading anything
  hang-init       never answer initialize
  bad-version     answer initialize with an unsupported protocolVersion
  init-error      answer initialize with a JSON-RPC error
  garbage-init    print non-JSON before the initialize response
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"
_out_lock = threading.Lock()
cancelled: list[object] = []
_cancel_events: dict[object, threading.Event] = {}
_pending_client_requests: dict[object, threading.Event] = {}
_client_responses: dict[object, dict] = {}

TOOLS = [
    "echo",  # {text} -> text
    "sleep",  # {seconds, text} -> text after delay (cancellable)
    "crash",  # exit(3) immediately, without answering
    "garbage",  # non-JSON line, then a valid answer
    "big",  # {bytes} answer bigger than the gateway's message limit
    "stderr_flood",  # {lines} write many stderr lines, then answer
    "env",  # answer with the process environment
    "argv",  # answer with sys.argv
    "dup",  # answer twice, plus an answer to an unknown id
    "wrong_id",  # answer a different id first, then the right one
    "progress",  # emit progress notifications on the caller's token, then answer
    "ask_client",  # send a server->client request; answer with what came back
    "error",  # JSON-RPC error response
    "cancelled",  # answer with ids we received notifications/cancelled for
    "bad_result",  # answer with a result that is not a valid CallToolResult
    "list_changed",  # emit notifications/tools/list_changed, then answer
    "leak_stderr",  # write SECRET_FOR_CHILD and a bearer header to stderr
]


def send(obj: dict) -> None:
    data = json.dumps(obj)
    with _out_lock:
        sys.stdout.write(data + "\n")
        sys.stdout.flush()


def raw(line: str) -> None:
    with _out_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def text_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def handle_call(rid: object, params: dict) -> None:
    name = params.get("name")
    args = params.get("arguments") or {}
    meta = params.get("_meta") or {}
    if name == "echo":
        send({"jsonrpc": "2.0", "id": rid, "result": text_result(str(args.get("text", "")))})
    elif name == "sleep":
        ev = threading.Event()
        _cancel_events[rid] = ev
        if not ev.wait(float(args.get("seconds", 0.1))):
            send({"jsonrpc": "2.0", "id": rid, "result": text_result(str(args.get("text", "")))})
    elif name == "crash":
        sys.stdout.flush()
        os._exit(3)
    elif name == "garbage":
        raw("this is not json\n")
        raw('{"jsonrpc": "2.0", "id": \n')  # truncated JSON
        raw("[]\n")  # empty batch
        raw('{"hello": "world"}\n')  # not JSON-RPC
        send({"jsonrpc": "2.0", "id": rid, "result": text_result("after garbage")})
    elif name == "big":
        size = int(args.get("bytes", 1024))
        send({"jsonrpc": "2.0", "id": rid, "result": text_result("x" * size)})
    elif name == "stderr_flood":
        n = int(args.get("lines", 1000))
        for i in range(n):
            sys.stderr.write(f"noise line {i} " + "y" * 100 + "\n")
        sys.stderr.flush()
        send({"jsonrpc": "2.0", "id": rid, "result": text_result(f"flooded {n}")})
    elif name == "env":
        send({"jsonrpc": "2.0", "id": rid, "result": text_result(json.dumps(dict(os.environ)))})
    elif name == "argv":
        send({"jsonrpc": "2.0", "id": rid, "result": text_result(json.dumps(sys.argv[1:]))})
    elif name == "dup":
        send({"jsonrpc": "2.0", "id": 999_999, "result": text_result("unknown id")})
        send({"jsonrpc": "2.0", "id": rid, "result": text_result("first")})
        send({"jsonrpc": "2.0", "id": rid, "result": text_result("second")})
    elif name == "wrong_id":
        other = rid + 1000 if isinstance(rid, int) else "x"
        send({"jsonrpc": "2.0", "id": other, "result": text_result("wrong")})
        send({"jsonrpc": "2.0", "id": str(rid), "result": text_result("stringified id")})
        time.sleep(0.05)
        send({"jsonrpc": "2.0", "id": rid, "result": text_result("right")})
    elif name == "progress":
        token = meta.get("progressToken")
        if token is not None:
            for i in range(1, 4):
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {"progressToken": token, "progress": i, "total": 3, "message": f"step {i}"},
                    }
                )
                time.sleep(0.02)
            # A forged token must not be relayed anywhere.
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progressToken": "omg-progress-999999", "progress": 1},
                }
            )
        send({"jsonrpc": "2.0", "id": rid, "result": text_result(f"token={token}")})
    elif name == "ask_client":
        req_id = f"srv-{rid}"
        ev = threading.Event()
        _pending_client_requests[req_id] = ev
        send({"jsonrpc": "2.0", "id": req_id, "method": "sampling/createMessage", "params": {}})
        ev.wait(5)
        send({"jsonrpc": "2.0", "id": rid, "result": text_result(json.dumps(_client_responses.get(req_id)))})
    elif name == "error":
        send(
            {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "bad args", "data": {"x": 1}}}
        )
    elif name == "cancelled":
        send({"jsonrpc": "2.0", "id": rid, "result": text_result(json.dumps(cancelled))})
    elif name == "bad_result":
        send({"jsonrpc": "2.0", "id": rid, "result": {"content": "not-a-list"}})
    elif name == "leak_stderr":
        sys.stderr.write(f"secret is {os.environ.get('SECRET_FOR_CHILD')}\n")
        sys.stderr.write("Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n")
        sys.stderr.flush()
        send({"jsonrpc": "2.0", "id": rid, "result": text_result("leaked")})
    elif name == "list_changed":
        send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        send({"jsonrpc": "2.0", "id": rid, "result": text_result("changed")})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool {name}"}})


def handle(msg: dict) -> None:
    method = msg.get("method")
    rid = msg.get("id")
    if method is None:
        # A response to one of our server->client requests.
        if rid in _pending_client_requests:
            _client_responses[rid] = {k: v for k, v in msg.items() if k != "jsonrpc"}
            _pending_client_requests[rid].set()
        return
    if method == "initialize":
        if MODE == "hang-init":
            return
        if MODE == "init-error":
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32600, "message": "nope"}})
            return
        if MODE == "garbage-init":
            raw("starting up...\n")
        version = "1999-01-01" if MODE == "bad-version" else msg["params"]["protocolVersion"]
        send(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "fake", "version": "0"},
                },
            }
        )
    elif method == "notifications/initialized":
        pass
    elif method == "notifications/cancelled":
        target = (msg.get("params") or {}).get("requestId")
        cancelled.append(target)
        ev = _cancel_events.get(target)
        if ev is not None:
            ev.set()
    elif method == "tools/list":
        tools = [{"name": n, "inputSchema": {"type": "object"}} for n in TOOLS]
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
    elif method == "tools/call":
        threading.Thread(target=handle_call, args=(rid, msg.get("params") or {}), daemon=True).start()
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}})


def main() -> None:
    if MODE == "crash-on-start":
        sys.exit(7)
    for line in sys.stdin:
        line = line.strip()
        if line:
            handle(json.loads(line))


if __name__ == "__main__":
    main()

"""Read-only requests made from inside the session's container, as the agent.

Requests run with `docker exec <container> curl`, so they take the agent's own
path: its proxy, the OneCLI gateway's Cedar decision for this agent, and the
credentials the gateway injects for it (the "placeholder" token is replaced
there; no secret ever reaches this process). What they return is what the agent
itself can see.

Only reads are possible: REST calls are GET; MCP calls are the protocol's
initialize/notifications and tools/call of tools the server's own snapshot marks
readOnlyHint. Every request is appended to ``log`` (method, URL, tool, arguments,
HTTP status, gateway decision when it refused), never with response bodies.
"""

import json
import subprocess

from .runtime import tool

JSON_HEADERS = ["-H", "Content-Type: application/json", "-H", "Accept: application/json, text/event-stream"]


class ProbeError(Exception):
    pass


class ContainerProbe:
    def __init__(self, container: str, log: list[dict]) -> None:
        self.container, self.log = container, log

    def _curl(self, args: list[str]) -> tuple[int, dict[str, str], str]:
        # Tagged so the runtime ontology does not learn from materialize's own probes
        # (src/runtime/capture.py PROBE_HEADER); the gateway records but the runtime skips them.
        argv = [tool("docker"), "exec", self.container, "curl", "-s", "-D", "-", "--max-time", "30",
                "-H", "X-NanoClaw-Ontology-Probe: 1", *args]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60).stdout
        head, _, body = out.replace("\r\n", "\n").partition("\n\n")
        while head.startswith("HTTP/") and body.startswith("HTTP/"):  # 100-continue / proxy CONNECT preambles
            head, _, body = body.partition("\n\n")
        lines = head.splitlines()
        status = int(lines[0].split()[1]) if lines and lines[0].startswith("HTTP/") else 0
        headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
        return status, headers, body

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> object:
        args = [url, "-H", "Accept: application/json"]
        for k, v in (headers or {}).items():
            args += ["-H", f"{k}: {v}"]
        status, _, body = self._curl(args)
        self.log.append({"method": "GET", "url": url, "status": status})
        if status != 200:
            raise ProbeError(f"GET {url}: HTTP {status}")
        return json.loads(body)

    def mcp(self, url: str, read_only_tools: set[str], headers: dict[str, str] | None = None) -> "MCPSession":
        return MCPSession(self, url, read_only_tools, headers or {})


class MCPSession:
    """A streamable-HTTP MCP session through the gateway, limited to read-only tools."""

    def __init__(self, probe: ContainerProbe, url: str, read_only_tools: set[str], headers: dict[str, str]) -> None:
        self.probe, self.url, self.read_only = probe, url, read_only_tools
        self.headers = [x for k, v in headers.items() for x in ("-H", f"{k}: {v}")]
        self.session_id: str | None = None
        self._id = 0
        self._post({"jsonrpc": "2.0", "id": self._next(), "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "materialize", "version": "1"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _next(self) -> int:
        self._id += 1
        return self._id

    def _post(self, message: dict) -> dict | None:
        args = ["-X", "POST", self.url, *JSON_HEADERS, *self.headers, "-d", json.dumps(message)]
        if self.session_id:
            args += ["-H", f"Mcp-Session-Id: {self.session_id}"]
        status, headers, body = self.probe._curl(args)
        self.session_id = self.session_id or headers.get("mcp-session-id")
        params = message.get("params") or {}
        self.probe.log.append({"method": "POST", "url": self.url, "rpc": message["method"], "tool": params.get("name"),
                               "arguments": params.get("arguments"), "status": status})
        if status not in (200, 202):
            raise ProbeError(f"{message['method']} {params.get('name') or ''}: HTTP {status}")
        if "id" not in message:
            return None
        payload = next((line[len("data: "):] for line in body.splitlines() if line.startswith("data: ")), body)
        reply = json.loads(payload)
        if "error" in reply:
            raise ProbeError(f"{message['method']}: {reply['error'].get('message')}")
        return reply["result"]

    def call(self, name: str, arguments: dict) -> object:
        """tools/call of a read-only tool; returns its JSON text content parsed, else the raw text,
        plus any embedded resource text under 'resources'."""
        if name not in self.read_only:
            raise ProbeError(f"refusing {name}: not a read-only tool")
        result = self._post({"jsonrpc": "2.0", "id": self._next(), "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        if result.get("isError"):
            raise ProbeError(f"{name}: " + " ".join(c.get("text", "") for c in result.get("content", []))[:300])
        texts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
        resources = [c["resource"].get("text", "") for c in result.get("content", []) if c.get("type") == "resource"]
        if resources:
            return {"text": texts, "resources": resources}
        try:
            return json.loads(texts[0]) if texts else None
        except json.JSONDecodeError:
            return texts[0]

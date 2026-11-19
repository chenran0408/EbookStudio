"""MCP stdio transport for the electronic book workbench agent."""

from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import sys


PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2024-11-05")
GUIDE_TOOL = "ebook_guide"
WORKFLOW_INSTRUCTIONS = (
    "用 ebook_status 检查本机工具。常规单栏扫描件用 xiaomi（Xiaomi-OCR-0 Q8），"
    "多栏杂志、插图或合并表格用 paddle（PaddleOCR-VL-1.6）。遵照用户指定的引擎。"
    "保留原文件，指定新的输出路径。先保存可编辑 .bookproj，"
    "再用 ebook_inspect 核对章节和目录，随后用 ebook_export 导出 EPUB，"
    "最后用 ebook_check（EPUBCheck）核验并报告结果。"
    f"调用 {GUIDE_TOOL} 读取完整指南。"
)


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _reject_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _error(code, message, request_id=None):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class _Session:
    def __init__(self, execute, actions):
        self.execute = execute
        self.actions = {f"ebook_{action}": action for action in actions}
        tools = {name: {"name": name, **actions[action]} for name, action in self.actions.items()}
        if "ebook_status" in tools:
            tools["ebook_status"]["description"] += (
                f" Call {GUIDE_TOOL} for OCR engine choice and the book project to validated EPUB workflow."
            )
        self.tools = [*tools.values(), {
            "name": GUIDE_TOOL,
            "description": "读取电子书工作台操作指南。涵盖 OCR 选型、可编辑 .bookproj、带目录的 EPUB 和 EPUBCheck。",
            "inputSchema": {"type": "object", "additionalProperties": False},
        }]
        self.version = None
        self.ready = False

    def handle(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(-32600, "Invalid Request")

        method = message.get("method")
        params = message.get("params", {})
        if "id" not in message and isinstance(method, str):
            if method == "notifications/initialized" and self.version and isinstance(params, dict):
                self.ready = True
            return None

        request_id = message.get("id")
        if type(request_id) not in (str, int):
            return _error(-32600, "Invalid Request")
        if not isinstance(method, str):
            return _error(-32600, "Invalid Request", request_id)
        if not isinstance(params, dict):
            return _error(-32602, "params must be an object", request_id)

        try:
            result = self.request(method, params)
        except RpcError as exc:
            return _error(exc.code, str(exc), request_id)

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def request(self, method, params):
        if method == "ping":
            return {}
        if method == "initialize":
            if self.version is not None:
                raise RpcError(-32600, "Server is already initialized")
            version = params.get("protocolVersion")
            if not isinstance(version, str) or not version:
                raise RpcError(-32602, "protocolVersion must be a nonempty string")
            self.version = version if version in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            server_info = {"name": "ebook-studio", "version": "1.0.0"}
            if self.version == "2025-11-25":
                server_info["description"] = (
                    f"本地电子书工作台。扫描件、网页、可编辑书稿和 EPUB 导出。调用 {GUIDE_TOOL} 查看指南。"
                )
            return {
                "protocolVersion": self.version,
                "capabilities": {"tools": {}},
                "serverInfo": server_info,
                "instructions": WORKFLOW_INSTRUCTIONS,
            }
        if method not in ("tools/list", "tools/call"):
            raise RpcError(-32601, f"Method not found: {method}")
        if not self.ready:
            raise RpcError(-32002, "Server not initialized")
        if method == "tools/list":
            if "cursor" in params:
                raise RpcError(-32602, "Invalid tools cursor")
            return {"tools": self.tools}
        return self.call(params)

    def call(self, params):
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise RpcError(-32602, "tools/call requires a name and an arguments object")
        if name != GUIDE_TOOL and name not in self.actions:
            raise RpcError(-32602, f"Unknown tool: {name}")

        try:
            if name == GUIDE_TOOL:
                if arguments:
                    raise ValueError(f"{GUIDE_TOOL} 不接受参数。")
                markdown = Path(__file__).with_name("agent.md").read_text(encoding="utf-8")
                payload = {"ok": True, "markdown": markdown}
            else:
                payload = self.execute({**arguments, "action": self.actions[name]})
        except Exception as exc:
            payload = {"ok": False, "error": str(exc) or type(exc).__name__}

        if not isinstance(payload, dict):
            raise RpcError(-32603, "Tool returned a result that is not an object")
        if name == "ebook_status" and payload.get("ok") is not False:
            payload = {**payload, "workflow": WORKFLOW_INSTRUCTIONS, "guide_tool": GUIDE_TOOL}
        try:
            text = _json(payload)
        except (TypeError, ValueError) as exc:
            raise RpcError(-32603, "Tool returned a result that cannot be encoded as JSON") from exc
        result = {"content": [{"type": "text", "text": text}], "isError": payload.get("ok") is False}
        if self.version != "2024-11-05":
            result["structuredContent"] = payload
        return result


def serve(execute, actions) -> int:
    """Read UTF-8 JSON-RPC lines until the client closes its input."""
    session = _Session(execute, actions)
    stdout = sys.stdout
    stdout_fd = stdout.fileno()
    stderr_fd = sys.stderr.fileno()
    # Preserve the protocol pipe before sending diagnostics and native stdout to stderr.
    with (
        os.fdopen(os.dup(stdout_fd), "wb") as protocol,
        os.fdopen(os.dup(stderr_fd), "w", encoding="utf-8", errors="backslashreplace", buffering=1) as log,
    ):
        os.dup2(stderr_fd, stdout_fd)
        try:
            with redirect_stdout(log), redirect_stderr(log):
                for line in sys.stdin.buffer:
                    try:
                        message = json.loads(line.decode("utf-8"), parse_constant=_reject_json_constant)
                    except ValueError:
                        response = _error(-32700, "Parse error")
                    else:
                        response = session.handle(message)
                    if response is not None:
                        protocol.write((_json(response) + "\n").encode("utf-8", errors="backslashreplace"))
                        protocol.flush()
        finally:
            stdout.flush()
            os.dup2(protocol.fileno(), stdout_fd)
    return 0

import json
from pathlib import Path
import subprocess
import sys
import unittest


APP = Path(__file__).resolve().parents[1]
SERVER = """
import os
import sys

from agent_mcp import serve

def execute(job):
    if job["action"] == "status":
        return {"ok": True, "tools": {"xiaomi": {"ready": True}}}
    if job.get("title") == "raise":
        raise ValueError("书页读取失败")
    if job.get("title") == "fail":
        return {"ok": False, "error": "缺少文件"}
    print("诊断日志")
    print("stderr log", file=sys.stderr)
    os.write(1, b"native stdout log\\n")
    return {"ok": True, "job": job}

raise SystemExit(serve(execute, {
    "create": {
        "description": "创建电子书",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    "status": {
        "description": "Inspect installed tools.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
}))
"""


def request(method, request_id=None, **params):
    message = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params:
        message["params"] = params
    return json.dumps(message, ensure_ascii=False).encode("utf-8")


def initialize(version):
    return request(
        "initialize", 1,
        protocolVersion=version, capabilities={}, clientInfo={"name": "tests", "version": "1"},
    )


class McpStdioTests(unittest.TestCase):
    def run_server(self, *messages):
        process = subprocess.run(
            [sys.executable, "-c", SERVER],
            cwd=APP,
            input=b"\n".join(messages) + b"\n",
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8"))
        responses = [json.loads(line) for line in process.stdout.decode("utf-8").splitlines()]
        return responses, process.stderr.decode("utf-8")

    def test_initializes_lists_and_calls_a_tool_over_clean_stdio(self):
        replies, log = self.run_server(
            request("ping", "测试"),
            initialize("2025-11-25"),
            request("notifications/initialized"),
            request("notifications/example"),
            request("tools/list", 2),
            request("tools/call", 3, name="ebook_create", arguments={
                "title": "电子书\n第二行",
                "action": "other",
            }),
            request("tools/call", 4, name="ebook_status"),
            request("tools/call", 5, name="ebook_guide"),
        )

        self.assertEqual([reply["id"] for reply in replies], ["测试", 1, 2, 3, 4, 5])
        self.assertTrue(all(reply["jsonrpc"] == "2.0" for reply in replies))
        self.assertEqual(replies[0]["result"], {})
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(replies[1]["result"]["capabilities"], {"tools": {}})
        instructions = replies[1]["result"]["instructions"]
        for term in ("xiaomi", "paddle", ".bookproj", "ebook_inspect", "ebook_export", "EPUBCheck", "原文件"):
            self.assertIn(term, instructions)
        self.assertIn("ebook_guide", replies[1]["result"]["serverInfo"]["description"])
        tools = {tool["name"]: tool for tool in replies[2]["result"]["tools"]}
        self.assertEqual(tools.keys(), {"ebook_create", "ebook_status", "ebook_guide"})
        self.assertIn("ebook_guide", tools["ebook_status"]["description"])
        self.assertIn("操作指南", tools["ebook_guide"]["description"])
        self.assertEqual(tools["ebook_guide"]["inputSchema"], {"type": "object", "additionalProperties": False})
        self.assertEqual(tools["ebook_create"], {
            "name": "ebook_create",
            "description": "创建电子书",
            "inputSchema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        })
        result = replies[3]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["job"], {"title": "电子书\n第二行", "action": "create"})
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        for line in ("诊断日志", "stderr log", "native stdout log"):
            self.assertIn(line, log)
        status = replies[4]["result"]["structuredContent"]
        self.assertEqual(status["guide_tool"], "ebook_guide")
        self.assertEqual(status["workflow"], instructions)
        self.assertTrue(status["tools"]["xiaomi"]["ready"])
        guide = replies[5]["result"]
        self.assertFalse(guide["isError"])
        self.assertEqual(guide["structuredContent"], {
            "ok": True,
            "markdown": (APP / "agent.md").read_text(encoding="utf-8"),
        })
        self.assertEqual(json.loads(guide["content"][0]["text"]), guide["structuredContent"])

    def test_negotiates_older_and_unsupported_versions(self):
        for requested, negotiated in (
            ("2024-11-05", "2024-11-05"),
            ("2025-06-18", "2025-06-18"),
            ("2099-01-01", "2025-11-25"),
        ):
            with self.subTest(requested=requested):
                replies, _ = self.run_server(
                    initialize(requested),
                    request("notifications/initialized"),
                    request("tools/list", 2),
                    request("tools/call", 3, name="ebook_create", arguments={"title": "书"}),
                    request("tools/call", 4, name="ebook_guide", arguments={}),
                )
                self.assertEqual(len(replies), 4)
                self.assertEqual(replies[0]["result"]["protocolVersion"], negotiated)
                self.assertIn("ebook_guide", replies[0]["result"]["instructions"])
                self.assertIn("ebook_guide", {tool["name"] for tool in replies[1]["result"]["tools"]})
                create, guide = (reply["result"] for reply in replies[2:])
                for result in (create, guide):
                    self.assertEqual("structuredContent" in result, negotiated != "2024-11-05")
                self.assertEqual(json.loads(create["content"][0]["text"])["job"]["title"], "书")
                self.assertEqual(
                    json.loads(guide["content"][0]["text"])["markdown"],
                    (APP / "agent.md").read_text(encoding="utf-8"),
                )

    def test_guide_rejects_arguments(self):
        replies, _ = self.run_server(
            initialize("2025-11-25"),
            request("notifications/initialized"),
            request("tools/call", 2, name="ebook_guide", arguments={"path": "another-guide.md"}),
            request("ping", 3),
        )
        result = replies[1]["result"]
        self.assertTrue(result["isError"])
        self.assertIn("不接受参数", result["structuredContent"]["error"])
        self.assertEqual(replies[2]["result"], {})

    def test_recovers_from_protocol_and_execution_errors(self):
        replies, _ = self.run_server(
            b"not JSON",
            b'{"jsonrpc":"2.0","method":"ping","id":NaN}',
            b'{"jsonrpc":"2.0","method":"\xff","id":7}',
            b"[]",
            request("tools/list", 8),
            initialize("2025-11-25"),
            request("notifications/initialized"),
            request("missing/method", 9),
            request("tools/call", 10, name="ebook_missing"),
            request("tools/call", 11, name="ebook_create", arguments=[]),
            request("tools/call", 12, name="ebook_create", arguments={"title": "raise"}),
            request("tools/call", 13, name="ebook_create", arguments={"title": "fail"}),
            request("ping", 14),
        )
        self.assertEqual(
            [(reply["id"], reply["error"]["code"]) for reply in replies if "error" in reply],
            [(None, -32700), (None, -32700), (None, -32700), (None, -32600),
             (8, -32002), (9, -32601), (10, -32602), (11, -32602)],
        )
        results = {reply["id"]: reply["result"] for reply in replies if "result" in reply}
        for request_id, message in ((12, "书页读取失败"), (13, "缺少文件")):
            result = results[request_id]
            self.assertTrue(result["isError"])
            self.assertEqual(json.loads(result["content"][0]["text"])["error"], message)
        self.assertEqual(results[14], {})


if __name__ == "__main__":
    unittest.main()

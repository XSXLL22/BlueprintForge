"""LLM provider 抽象与「生成 → 验证 → 修复」闭环的单测（纯 mock，无真实 API）。"""
import http.server
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from hdc import llm
from hdc.llm import (
    LLMError,
    OllamaProvider,
    design_with_fix,
    load_config,
    parse_design,
    provider_from_config,
)
from hdc.toolchain import detect

TC = detect()

# 最小合法设计：直通线（够小、可综合、自检）
RTL = "module passthru (input wire a, output wire b); assign b = a; endmodule\n"
TB = """`timescale 1ns / 1ps
module tb_passthru;
    reg a;
    wire b;
    integer fail_count;
    passthru dut (.a(a), .b(b));
    task check;
        input [255:0] name;
        input ok;
        begin
            if (ok)      $display("CHECK %0s: PASS", name);
            else begin
                $display("CHECK %0s: FAIL", name);
                fail_count = fail_count + 1;
            end
        end
    endtask
    initial begin
        fail_count = 0;
        a = 1'b0; #10; check("zero", b === 1'b0);
        a = 1'b1; #10; check("one",  b === 1'b1);
        if (fail_count == 0)
            $display("SIM_RESULT: PASS");
        else
            $display("SIM_RESULT: FAIL");
        $finish;
    end
endmodule
"""


def _model_output(rtl: str, tb: str) -> str:
    return json.dumps({
        "project": "passthru",
        "requirement": "直通线",
        "rtl": rtl,
        "tb": tb,
        "design_json": {
            "project": "passthru",
            "requirement": "直通线",
            "interface": {"inputs": ["a"], "outputs": ["b"]},
        },
        "state_machine_md": "",
        "concept_md": "# 构想\n直通。\n",
    }, ensure_ascii=False)


def _good() -> str:
    return _model_output(RTL, TB)


def _bad() -> str:
    return _model_output(RTL, TB.replace("SIM_RESULT: PASS", "SIM_RESULT: OK"))


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.responses.pop(0)


class TestConfig(unittest.TestCase):
    def test_load_config_env_override(self):
        with mock.patch.object(llm, "_config_path", return_value=Path("/nonexistent/hdc.json")):
            with mock.patch.dict(os.environ, {"HDC_PROVIDER": "ollama", "HDC_MODEL": "qwen2"}):
                cfg = load_config()
        self.assertEqual(cfg["provider"], "ollama")
        self.assertEqual(cfg["model"], "qwen2")

    def test_provider_ollama_needs_no_key(self):
        p = provider_from_config({"provider": "ollama", "model": "llama3.1"})
        self.assertIsInstance(p, OllamaProvider)

    def test_provider_openai_missing_key_raises(self):
        with self.assertRaises(LLMError):
            provider_from_config({"provider": "openai"})

    def test_provider_unknown_raises(self):
        with self.assertRaises(LLMError):
            provider_from_config({"provider": "deepseek-ultra"})


class TestParseDesign(unittest.TestCase):
    def test_parse_strips_code_fence(self):
        text = "```json\n" + _good() + "\n```"
        d = parse_design(text)
        self.assertEqual(d.project, "passthru")
        self.assertIn("module passthru", d.rtl)
        self.assertIn("tb_passthru", d.tb)

    def test_parse_minimal_without_design_json(self):
        data = {"project": "x", "rtl": "module x; endmodule", "tb": "module tb_x; endmodule"}
        d = parse_design(json.dumps(data))
        self.assertEqual(d.project, "x")
        self.assertEqual(d.design_json["interface"], {"inputs": [], "outputs": []})

    def test_parse_missing_fields_raises(self):
        with self.assertRaises(LLMError):
            parse_design('{"project": "x"}')


@unittest.skipUnless(TC.can_simulate, "iverilog/vvp 未安装，跳过修复闭环测试")
class TestDesignWithFix(unittest.TestCase):
    def _run(self, responses, max_fix_rounds=3):
        p = FakeProvider(responses)
        tmp = tempfile.mkdtemp(prefix="hdc_llm_")
        out = design_with_fix(p, "直通线", Path(tmp), max_fix_rounds=max_fix_rounds,
                              run_synth=TC.can_synthesize)
        return p, out

    def test_converges_after_fix(self):
        p, out = self._run([_bad(), _good()])
        self.assertTrue(out.ok)
        self.assertEqual(len(p.calls), 2)
        # 第二轮应固定 project 并附带错误反馈
        self.assertIn("passthru", p.calls[1][0])
        self.assertIn("SIM_RESULT", p.calls[1][1])

    def test_max_rounds_gives_up(self):
        p, out = self._run([_bad(), _bad(), _bad()], max_fix_rounds=3)
        self.assertFalse(out.ok)
        self.assertEqual(len(p.calls), 3)
        self.assertTrue(out.errors())


class _OpenAIHandler(http.server.BaseHTTPRequestHandler):
    content = ""  # 类级共享：每次请求返回的 choices[0].message.content

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        body = json.dumps({"choices": [{"message": {"content": self.content}}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(TC.can_simulate, "iverilog/vvp 未安装，跳过 HTTP 端到端测试")
class TestOpenAIProviderHTTP(unittest.TestCase):
    """真实走 urllib → 本地 HTTP 服务器，验证 OpenAIProvider 与完整闭环。"""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _OpenAIHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _provider(self):
        return llm.OpenAIProvider(api_key="test", model="m",
                                  base_url=f"http://127.0.0.1:{self.port}")

    def test_chat_returns_content(self):
        _OpenAIHandler.content = "hello-verilog"
        self.assertEqual(self._provider().chat("sys", "user"), "hello-verilog")

    def test_end_to_end_design(self):
        _OpenAIHandler.content = _good()
        tmp = tempfile.mkdtemp(prefix="hdc_http_")
        out = design_with_fix(self._provider(), "直通线", Path(tmp),
                              run_synth=TC.can_synthesize)
        self.assertTrue(out.ok)


if __name__ == "__main__":
    unittest.main()

"""设计产物契约（LLM 自由生成）的单测：产物往返 + 最小硬契约自检。"""
import tempfile
import unittest
from pathlib import Path

from hdc.design import Design, load_artifacts, verify_design, write_artifacts
from hdc.toolchain import detect
from tests.examples import counter_rtl, counter_tb

TC = detect()

# 最小合法设计：4 位计数器（异步低复位），完全脱离流水灯模板。
# RTL 与 TB 都读自 `examples/counter/` —— 示例是事实来源，测试不抄第二份。
RTL = counter_rtl()
TB = counter_tb()


def _counter_design() -> Design:
    return Design(
        project="counter",
        requirement="4 位计数器，异步低复位，每个时钟 +1 自动溢出回绕",
        rtl=RTL,
        tb=TB,
        design_json={
            "project": "counter",
            "requirement": "4 位计数器，异步低复位",
            "interface": {"inputs": ["clk", "rst_n"], "outputs": ["count"]},
        },
    )


class TestDesignArtifacts(unittest.TestCase):
    def test_write_and_load_roundtrip(self):
        d = _counter_design()
        d.state_machine_md = "```mermaid\nstateDiagram\n  [*] --> COUNT\n```\n"
        d.concept_md = "# 设计构想\n一个 4 位计数器。\n"
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_artifacts(d, Path(tmp))
            self.assertTrue(paths["rtl"].exists())
            self.assertTrue(paths["tb"].exists())
            self.assertTrue((Path(tmp) / "design.json").exists())
            loaded = load_artifacts(Path(tmp))
            self.assertEqual(loaded.project, "counter")
            self.assertEqual(loaded.rtl, RTL)
            self.assertEqual(loaded.tb, TB)
            self.assertEqual(loaded.design_json["interface"]["inputs"], ["clk", "rst_n"])
            self.assertIn("stateDiagram", loaded.state_machine_md)


@unittest.skipUnless(TC.can_simulate, "iverilog/vvp 未安装，跳过验证编排测试")
class TestVerifyDesign(unittest.TestCase):
    def _run(self, mutate=None) -> object:
        d = _counter_design()
        if mutate:
            mutate(d)
        tmp = tempfile.mkdtemp(prefix="hdc_design_")
        return verify_design(d, Path(tmp), run_synth=TC.can_synthesize)

    def test_counter_passes(self):
        out = self._run()
        self.assertIsNotNone(out.sim)
        self.assertTrue(out.sim.passed, out.sim.log)
        if TC.can_synthesize:
            self.assertTrue(out.synth.ok, out.synth.log)
        self.assertTrue(out.ok)

    def test_catches_wrong_tb_top(self):
        def mutate(d):
            d.tb = d.tb.replace("module tb_counter", "module wrong_tb")
        out = self._run(mutate)
        self.assertFalse(out.sim.passed)
        self.assertEqual(out.sim.error_class, "compile_error")

    def test_catches_missing_sim_result(self):
        # 只替换标记字符串（保留合法 Verilog），让仿真输出里不再出现
        # 精确子串 "SIM_RESULT: PASS"，从而触发断言失败分类。
        def mutate(d):
            d.tb = d.tb.replace('SIM_RESULT: PASS', 'SIM_RESULT: OK')
        out = self._run(mutate)
        self.assertFalse(out.sim.passed)
        self.assertEqual(out.sim.error_class, "assertion_failure")

    def test_errors_lists_classification(self):
        def mutate(d):
            d.tb = d.tb.replace('SIM_RESULT: PASS', 'SIM_RESULT: OK')
        out = self._run(mutate)
        self.assertTrue(any("assertion_failure" in e for e in out.errors()))


if __name__ == "__main__":
    unittest.main()

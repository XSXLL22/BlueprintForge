"""端到端验收：完整闭环 + 错误注入（依赖 iverilog/yosys，未安装则跳过）。"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from hdc.inject import BUG_TYPES
from hdc.pipeline import build
from hdc.toolchain import detect

TC = detect()
_FAST = Path(__file__).resolve().parent.parent / "specs" / "led_chaser_fast.json"


@unittest.skipUnless(TC.can_simulate, "iverilog/vvp 未安装，跳过仿真相关测试")
class TestEndToEnd(unittest.TestCase):
    def _build(self, **kw):
        tmp = tempfile.mkdtemp(prefix="hdc_e2e_")
        return build(_FAST, Path(tmp), run_synth=TC.can_synthesize, **kw)

    def test_clean_build_passes(self):
        r = self._build()
        self.assertIsNotNone(r.sim)
        self.assertTrue(r.sim.passed, r.sim.log)
        if TC.can_synthesize:
            self.assertTrue(r.synth.ok, r.synth.log)

    def test_every_injection_is_caught(self):
        for bug in BUG_TYPES:
            with self.subTest(bug=bug):
                r = self._build(inject=bug)
                self.assertIsNotNone(r.sim)
                self.assertFalse(r.sim.passed, f"{bug} 未被 testbench 检出:\n{r.sim.sim_output}")

    def test_output_tree_complete(self):
        r = self._build()
        for rel in ["rtl/led_chaser.v", "tb/tb_led_chaser.v", "sim/sim.log",
                    "diagrams/block_diagram.svg", "diagrams/state_diagram.svg",
                    "docs/spec.json", "docs/report.md", "README.md"]:
            self.assertTrue((r.out_dir / rel).exists(), f"缺少产物 {rel}")


@unittest.skipUnless(TC.can_simulate, "iverilog/vvp 未安装，跳过仿真相关测试")
class TestRelativeOutDir(unittest.TestCase):
    """回归：用相对 --out 路径构建时，vvp 子进程 cwd 不应使 sim.vvp 路径错位。"""

    def test_relative_out_dir(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            try:
                os.chdir(d)
                shutil.copy(_FAST, "spec.json")
                r = build(Path("spec.json"), Path("out"), run_synth=TC.can_synthesize)
                self.assertIsNotNone(r.sim)
                self.assertTrue(r.sim.passed, r.sim.log)
                if TC.can_synthesize:
                    self.assertTrue(r.synth.ok, r.synth.log)
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()

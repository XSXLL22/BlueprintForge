"""RTL → 74 系列门级网表（yosys techmap 到 vendor Liberty 库）的单测。"""
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from hdc.pcb import synth74
from hdc.toolchain import detect
from tests.examples import counter_rtl

TC = detect()

#: 基准电路读自 `examples/counter/counter.v` —— 测试里不再抄一份。用户
#: `python -m hdc examples/counter/counter.v --pcb` 跑的就是这一份文件。
COUNTER_RTL = counter_rtl()

#: 只在本模块用到的最小组合逻辑，没有对应的示例目录，就地写着更清楚。
COMB_RTL = """module gate2 (
    input  wire a,
    input  wire b,
    output wire y
);
    assign y = a & b;
endmodule
"""


@unittest.skipUnless(TC.can_synthesize, "yosys 未安装，跳过 74 系列综合测试")
class TestSynthesize74(unittest.TestCase):
    def _synth(self, rtl_text: str, project: str) -> synth74.Netlist74:
        tmp = Path(tempfile.mkdtemp(prefix="hdc_synth74_"))
        rtl = tmp / f"{project}.v"
        rtl.write_text(rtl_text, encoding="utf-8")
        return synth74.synthesize(TC, rtl, project, tmp / "pcb")

    def test_counter_maps_to_register_and_adder(self):
        nl = self._synth(COUNTER_RTL, "counter")
        kinds = Counter(c.type for c in nl.cells)
        # 4 位计数器 = 4 个触发器位 + 一个 4 位加法器
        self.assertEqual(kinds["74AC283_1x1ADD4"], 1, kinds)
        self.assertEqual(kinds["74AC273_8x1DFFR"], 4, kinds)
        self.assertEqual(sum(kinds.values()), 5, kinds)

    def test_ports_are_preserved_with_direction_and_width(self):
        nl = self._synth(COUNTER_RTL, "counter")
        ports = {p.name: p for p in nl.ports}
        self.assertEqual(ports["clk"].direction, "input")
        self.assertEqual(len(ports["clk"].bits), 1)
        self.assertEqual(ports["count"].direction, "output")
        self.assertEqual(len(ports["count"].bits), 4)

    def test_every_cell_type_is_in_the_chip_knowledge_base(self):
        from hdc.pcb import cells

        nl = self._synth(COUNTER_RTL, "counter")
        for c in nl.cells:
            self.assertTrue(
                cells.is_net_alias(c.type) or c.type in cells.CELLS,
                f"{c.type} 未收录在芯片知识库",
            )

    def test_combinational_design_maps_to_gates(self):
        nl = self._synth(COMB_RTL, "gate2")
        self.assertTrue(nl.cells, "组合逻辑应至少综合出一个门")
        for c in nl.cells:
            self.assertTrue(c.type.startswith("74"), c.type)

    def test_artifacts_are_written(self):
        nl = self._synth(COUNTER_RTL, "counter")
        self.assertTrue(nl.netlist_json.exists())
        self.assertTrue((nl.netlist_json.parent / "synth74.log").exists())
        self.assertIn("74AC273", nl.stat)

    def test_net_names_prefer_readable_rtl_names(self):
        nl = self._synth(COUNTER_RTL, "counter")
        readable = set(nl.net_names.values())
        self.assertIn("clk", readable)
        self.assertIn("rst_n", readable)
        self.assertTrue(any(n.startswith("count") for n in readable), readable)

    def test_missing_top_module_reports_synthesis_error(self):
        tmp = Path(tempfile.mkdtemp(prefix="hdc_synth74_"))
        rtl = tmp / "bad.v"
        rtl.write_text(COMB_RTL, encoding="utf-8")
        with self.assertRaises(synth74.Synth74Error):
            synth74.synthesize(TC, rtl, "no_such_top", tmp / "pcb")


class TestToolchainGuard(unittest.TestCase):
    def test_without_yosys_raises_actionable_error(self):
        from hdc.toolchain import Toolchain

        with tempfile.TemporaryDirectory() as tmp:
            rtl = Path(tmp) / "x.v"
            rtl.write_text(COMB_RTL, encoding="utf-8")
            with self.assertRaises(synth74.Synth74Error) as ctx:
                synth74.synthesize(Toolchain(), rtl, "gate2", Path(tmp) / "pcb")
        self.assertIn("yosys", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

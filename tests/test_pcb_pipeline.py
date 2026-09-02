"""T1.7 全链路编排单测。

分三层，按「要装什么」排：

* **不需要任何工具**：`.kicad_pro` 的内容、`PcbResult` 的结论逻辑（`ok` /
  `errors()`）。结论逻辑是这条流水线对外的判据，必须逐条钉住 —— 它说 `ok` 就
  意味着「这套文件可以直接送厂」。
* **只需要 yosys**：`run_layout=False` 时只做到原理图，并把没做的那一步如实
  记进 `skipped`。
* **yosys + KiCad**：把计数器从 RTL 一路做到嘉立创可上传的 ZIP，判据是产物齐全、
  DRC 干净、地平面完整、CPL 与板上元件逐个对得上。

判定尽量不看实现：产物看文件系统，元件清单看 CSV，不看 `build_pcb` 的中间变量。
"""
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from hdc.pcb import kicad, peripheral, pipeline
from hdc.pcb.cells import spec_for
from hdc.pcb.manufacture import DrcReport, Fabrication
from hdc.pcb.pack import GND_NET, VCC_NET, Assembly, ChipInstance, PinConn
from hdc.pcb.pipeline import PcbResult, build_pcb, write_project
from hdc.pcb.synth74 import Netlist74
from hdc.toolchain import detect
from tests.test_pcb_synth74 import COUNTER_RTL

TC = detect()

#: 有 yosys 才能综合；有 kicad-cli 才能摆件、铺铜、导出。
NEEDS_SYNTH = unittest.skipUnless(TC.can_synthesize, "yosys 未安装")
NEEDS_KICAD = unittest.skipUnless(TC.can_synthesize and kicad.find_cli(),
                                  "需要 yosys + kicad-cli")


def _rows(text: str) -> list[list[str]]:
    import csv
    import io
    return [r for r in csv.reader(io.StringIO(text)) if r]


# --- 结论逻辑（不需要任何工具） ----------------------------------------------

def _result(**kw) -> PcbResult:
    """凑一个 `PcbResult` 出来，只为检验它的结论逻辑。

    默认是「一切顺利」的那一版：没跳过、都布通了、地平面一整块、DRC 干净、制造
    文件在手。每个测试改一处，看结论跟不跟着变。
    """
    asm = Assembly(project="stub")
    asm.chips = [ChipInstance("U1", spec_for("74AC273_8x1DFFR"), 1)]
    asm.connections = [PinConn("U1", 20, VCC_NET, "VCC"),
                       PinConn("U1", 10, GND_NET, "GND")]
    here = Path("stub")
    fields = dict(
        project="stub", out_dir=here, netlist=Netlist74("stub", [], []),
        assembly=asm, board=peripheral.build_board(asm),
        schematic_file=here / "stub.kicad_sch",
        pcb_file=here / "stub.kicad_pcb", zone_islands=1,
        drc=DrcReport(ok=True, path=here / "drc.rpt", text=""),
        fabrication=Fabrication(project="stub", gerbers=(), drill=here,
                                bom=here, cpl=here, schematic_pdf=here,
                                board_pdf=here, archive=here),
    )
    return PcbResult(**{**fields, **kw})


class TestVerdict(unittest.TestCase):
    def test_a_clean_run_is_ok_and_lists_no_errors(self):
        self.assertTrue(_result().ok)
        self.assertEqual(_result().errors(), ())

    def test_an_unrouted_net_is_named_and_sinks_the_verdict(self):
        r = _result(unrouted=("clk",))
        self.assertFalse(r.ok)
        self.assertIn("clk", " ".join(r.errors()))

    def test_a_fragmented_ground_plane_sinks_the_verdict(self):
        """地平面被切成几块 = 有 GND 焊盘的地是浮的，不能送厂。"""
        r = _result(zone_islands=2)
        self.assertFalse(r.ok)
        self.assertIn("2", " ".join(r.errors()))

    def test_drc_violations_are_carried_out_verbatim(self):
        r = _result(drc=DrcReport(ok=False, path=Path("drc.rpt"),
                                 text="[clearance]: 间距不够\n其它废话\n"))
        self.assertFalse(r.ok)
        self.assertIn("[clearance]: 间距不够", " ".join(r.errors()))

    def test_a_skipped_step_counts_as_not_finished(self):
        r = _result(skipped=("布局布线：没装 KiCad",))
        self.assertFalse(r.ok)
        self.assertIn("没装 KiCad", " ".join(r.errors()))

    def test_without_fabrication_files_the_run_is_not_ok(self):
        self.assertFalse(_result(fabrication=None).ok)

    def test_chips_and_warnings_come_from_the_assembly_and_board(self):
        r = _result()
        self.assertEqual(r.chips, {"74HC273": 1})
        self.assertEqual(r.warnings,
                         tuple(r.assembly.warnings) + tuple(r.board.warnings))


class TestProjectFile(unittest.TestCase):
    """`.kicad_pro` —— 让原理图与板图作为同一个工程被双击打开。"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.board = Path(self._tmp.name) / "counter.kicad_pcb"
        self.board.write_text("(kicad_pcb)\n", encoding="utf-8")
        self.path = write_project(self.board)

    def tearDown(self):
        self._tmp.cleanup()

    def test_it_lands_next_to_the_board_with_the_same_stem(self):
        self.assertEqual(self.path.name, "counter.kicad_pro")
        self.assertEqual(self.path.parent, self.board.parent)

    def test_it_is_valid_json_with_a_single_root_sheet(self):
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(doc["meta"]["filename"], "counter.kicad_pro")
        self.assertEqual(len(doc["sheets"]), 1)
        self.assertEqual(doc["sheets"][0][1], "Root")

    def test_it_overrides_no_design_rule(self):
        """设计规则一项不写 —— 留空由 KiCad 填默认值，不悄悄改变 DRC 的判据。"""
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(doc["board"]["design_settings"], {})
        self.assertEqual(doc["net_settings"], {})

    def test_the_same_board_always_yields_the_same_file(self):
        first = self.path.read_bytes()
        self.path.unlink()
        self.assertEqual(write_project(self.board).read_bytes(), first)


# --- 只做到原理图（只需要 yosys） --------------------------------------------

def _write_rtl(into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    rtl = into / "counter.v"
    rtl.write_text(COUNTER_RTL, encoding="utf-8")
    return rtl


@NEEDS_SYNTH
class TestSchematicOnly(unittest.TestCase):
    """`run_layout=False`：不需要封装库，所以没装 KiCad 也能出原理图。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.out = Path(cls._tmp.name)
        cls.r = build_pcb(_write_rtl(cls.out / "src"), "counter",
                          cls.out / "pcb", run_layout=False)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_schematic_is_written_and_the_board_is_not(self):
        self.assertTrue(self.r.schematic_file.is_file())
        self.assertGreater(self.r.schematic_file.stat().st_size, 0)
        self.assertIsNone(self.r.pcb_file)
        self.assertIsNone(self.r.fabrication)

    def test_the_missing_step_is_recorded_and_the_run_is_not_ok(self):
        self.assertEqual(len(self.r.skipped), 1)
        self.assertIn("布局布线", self.r.skipped[0])
        self.assertFalse(self.r.ok)

    def test_the_counter_still_packs_into_two_74hc_chips(self):
        """综合与装箱与布局无关，跳过布线不该影响芯片清单。"""
        self.assertEqual(self.r.chips, {"74HC273": 1, "74HC283": 1})


# --- 全链路（yosys + KiCad） --------------------------------------------------

@NEEDS_KICAD
class TestFullChain(unittest.TestCase):
    """RTL → 嘉立创可上传 ZIP。整条链只跑一次，导出很慢。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.out = Path(cls._tmp.name)
        cls.r = build_pcb(_write_rtl(cls.out / "src"), "counter", cls.out / "pcb")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_whole_chain_reports_success(self):
        """这一条就是 T1.7 的验收：它为真才等于「可以直接送厂」。"""
        self.assertEqual(self.r.errors(), ())
        self.assertTrue(self.r.ok)

    def test_nothing_was_skipped_and_everything_got_routed(self):
        self.assertEqual(self.r.skipped, ())
        self.assertEqual(self.r.unrouted, ())

    def test_the_ground_plane_is_one_piece_and_covers_the_board(self):
        self.assertEqual(self.r.zone_islands, 1)
        self.assertGreater(self.r.zone_area, 1000.0)

    def test_drc_is_clean_and_the_report_is_kept_either_way(self):
        self.assertIsNotNone(self.r.drc)
        self.assertTrue(self.r.drc.path.is_file(), "DRC 报告没留下来")
        self.assertTrue(self.r.drc.ok, self.r.drc.text)

    def test_every_promised_file_exists_and_is_not_empty(self):
        promised = [self.r.schematic_file, self.r.pcb_file, self.r.project_file,
                    *self.r.fabrication.files]
        for path in promised:
            with self.subTest(path.name):
                self.assertTrue(path.is_file(), f"{path} 没生成")
                self.assertGreater(path.stat().st_size, 0, f"{path} 是空的")

    def test_the_uploadable_zip_holds_the_gerbers_and_the_drill_file(self):
        fab = self.r.fabrication
        with zipfile.ZipFile(fab.archive) as zf:
            self.assertEqual(sorted(zf.namelist()),
                             sorted([p.name for p in fab.gerbers]
                                    + [fab.drill.name]))

    def test_the_placement_file_lists_exactly_the_components_on_the_board(self):
        """CPL 与板子必须逐个对上 —— 少一个就少贴一颗，多一个贴片机会报错。"""
        rows = _rows(self.r.fabrication.cpl.read_text(encoding="utf-8"))[1:]
        self.assertEqual(sorted(row[0] for row in rows),
                         sorted(c.ref for c in self.r.board.components))

    def test_the_bom_lists_the_chips_that_synthesis_chose(self):
        text = self.r.fabrication.bom.read_text(encoding="utf-8")
        for part in self.r.chips:
            with self.subTest(part):
                self.assertIn(part, text)

    def test_the_chips_are_the_two_the_counter_needs(self):
        self.assertEqual(self.r.chips, {"74HC273": 1, "74HC283": 1})

    def test_the_artifacts_all_land_under_the_requested_directory(self):
        for path in (self.r.schematic_file, self.r.pcb_file, self.r.project_file,
                     *self.r.fabrication.files):
            with self.subTest(path.name):
                self.assertTrue(path.is_absolute() or path.exists())
                self.assertIn(self.r.out_dir, path.parents)


@NEEDS_KICAD
class TestBoardWithoutFabrication(unittest.TestCase):
    """`run_manufacture=False`：做到板图与 DRC 为止，不导出 Gerber。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.out = Path(cls._tmp.name)
        cls.r = build_pcb(_write_rtl(cls.out / "src"), "counter",
                          cls.out / "pcb", run_manufacture=False)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_board_is_there_but_the_fabrication_files_are_not(self):
        self.assertTrue(self.r.pcb_file.is_file())
        self.assertTrue(self.r.project_file.is_file())
        self.assertIsNone(self.r.fabrication)
        self.assertFalse(list(self.r.out_dir.glob("gerber/*")))

    def test_the_board_itself_is_still_checked_and_clean(self):
        self.assertEqual(self.r.zone_islands, 1)
        self.assertEqual(self.r.unrouted, ())
        self.assertTrue(self.r.drc.ok, self.r.drc.text)

    def test_stopping_early_is_recorded_and_not_called_success(self):
        self.assertEqual(len(self.r.skipped), 1)
        self.assertIn("制造文件", self.r.skipped[0])
        self.assertFalse(self.r.ok)


if __name__ == "__main__":
    unittest.main()

"""T1.5（其一）封装库读取单测。

结构层用测试自己写的一个最小 `.kicad_mod`（不需要装 KiCad）；几何层拿真实
KiCad 库里的 DIP-14 与数据手册事实（14 脚、脚距 2.54、行距 7.62）对照。

旋转变换的期望值不是从公式推的，是用 KiCad 自带 Python 摆了一片 DIP-14 到
(50, 50) 再读回焊盘绝对坐标量出来的 —— 独立事实来源。
"""
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hdc.pcb import footprints, kicad

MINIMAL = """(footprint "TEST_2P"
\t(version 20260206)
\t(generator "hand")
\t(layer "F.Cu")
\t(descr "两脚测试封装")
\t(attr through_hole)
\t(property "Reference" "REF**" (at 0 -2 0) (layer "F.SilkS")
\t\t(effects (font (size 1 1) (thickness 0.15))))
\t(property "Value" "TEST_2P" (at 0 4 0) (layer "F.Fab")
\t\t(effects (font (size 1 1) (thickness 0.15))))
\t(fp_line (start -1.5 -1.5) (end 6.5 3.5)
\t\t(stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
\t(pad "1" thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8)
\t\t(layers "*.Cu" "*.Mask"))
\t(pad "2" thru_hole oval (at 5 0) (size 2.0 1.4) (drill 0.9)
\t\t(layers "*.Cu" "*.Mask"))
\t(pad "3" smd rect (at 2.5 2) (size 1.0 1.0) (layers "F.Cu" "F.Paste" "F.Mask"))
)
"""


def _sexp_ok(text: str) -> bool:
    """括号是否配平（字符串里的括号不算）。"""
    depth = 0
    for token in re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"', text):
        depth += 1 if token == "(" else -1 if token == ")" else 0
        if depth < 0:
            return False
    return depth == 0


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        lib = self.root / "Test.pretty"
        lib.mkdir()
        (lib / "TEST_2P.kicad_mod").write_text(MINIMAL, encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)
        self.fp = footprints.load("Test:TEST_2P", root=self.root)
class TestParsing(_Fixture):
    def test_identity_comes_from_the_footprint_id(self):
        self.assertEqual((self.fp.lib, self.fp.name), ("Test", "TEST_2P"))
        self.assertEqual(self.fp.id, "Test:TEST_2P")

    def test_pads_keep_their_numbers_and_local_positions(self):
        by_num = {p.number: p for p in self.fp.pads}
        self.assertEqual(sorted(by_num), ["1", "2", "3"])
        self.assertEqual((by_num["2"].x, by_num["2"].y), (5.0, 0.0))
        self.assertEqual(by_num["2"].drill, 0.9)

    def test_pad_radius_covers_the_larger_dimension(self):
        """椭圆焊盘按长边算等效半径 —— 布线避让宁可保守。"""
        by_num = {p.number: p for p in self.fp.pads}
        self.assertAlmostEqual(by_num["2"].radius, 1.0)
        self.assertAlmostEqual(by_num["1"].radius, 0.8)

    def test_through_hole_pads_span_both_layers_smd_only_the_front(self):
        by_num = {p.number: p for p in self.fp.pads}
        self.assertEqual(by_num["1"].layers, (0, 1))
        self.assertEqual(by_num["3"].layers, (0,))

    def test_smd_pads_have_no_drill(self):
        by_num = {p.number: p for p in self.fp.pads}
        self.assertEqual(by_num["3"].drill, 0.0)

    def test_courtyard_becomes_the_bounding_box(self):
        self.assertEqual(self.fp.bbox, (-1.5, -1.5, 6.5, 3.5))

    def test_loading_is_cached_so_the_same_object_comes_back(self):
        again = footprints.load("Test:TEST_2P", root=self.root)
        self.assertIs(again, self.fp)

    def test_missing_footprint_names_the_file_it_looked_for(self):
        with self.assertRaises(footprints.FootprintError) as ctx:
            footprints.load("Test:NOPE", root=self.root)
        self.assertIn("NOPE", str(ctx.exception))
class TestPlacementMath(_Fixture):
    #: 用 KiCad 自带 Python 量出来的：DIP-14 摆在 (50,50)，局部 (7.62,15.24)。
    MEASURED = {0: (57.62, 65.24), 90: (65.24, 42.38),
                180: (42.38, 34.76), 270: (34.76, 57.62)}

    def test_rotation_matches_what_kicad_itself_computes(self):
        for rot, expect in self.MEASURED.items():
            got = footprints.place((7.62, 15.24), (50.0, 50.0), rot)
            self.assertAlmostEqual(got[0], expect[0], places=3, msg=f"rot={rot}")
            self.assertAlmostEqual(got[1], expect[1], places=3, msg=f"rot={rot}")

    def test_pad_positions_are_absolute_after_placing(self):
        got = self.fp.pad_positions((10.0, 20.0), 0)
        self.assertEqual(got["1"], (10.0, 20.0))
        self.assertEqual(got["2"], (15.0, 20.0))

    def test_bbox_of_a_rotated_footprint_still_encloses_every_pad(self):
        for rot in (0, 90, 180, 270):
            x1, y1, x2, y2 = self.fp.placed_bbox((30.0, 40.0), rot)
            for x, y in self.fp.pad_positions((30.0, 40.0), rot).values():
                self.assertTrue(x1 <= x <= x2 and y1 <= y <= y2, f"rot={rot}")


class TestRendering(_Fixture):
    def setUp(self):
        super().setUp()
        self.text = "\n".join(self.fp.render(
            ref="U9", value="74HC00", at=(100.0, 60.0), rotation=90,
            nets={"1": (3, "clk"), "2": (0, "")}, uid="dead-beef"))

    def test_output_is_balanced_s_expression_starting_with_footprint(self):
        self.assertTrue(self.text.lstrip().startswith('(footprint "Test:TEST_2P"'))
        self.assertTrue(_sexp_ok(self.text))

    def test_placement_and_uuid_are_written(self):
        self.assertIn("(at 100 60 90)", self.text)
        self.assertIn('(uuid "dead-beef")', self.text)

    def test_reference_and_value_properties_are_overwritten(self):
        self.assertIn('(property "Reference" "U9"', self.text)
        self.assertIn('(property "Value" "74HC00"', self.text)
        self.assertNotIn("REF**", self.text)

    def test_each_pad_carries_its_net_and_a_uuid(self):
        pad1 = self.text[self.text.index('(pad "1"'):self.text.index('(pad "2"')]
        self.assertIn('(net 3 "clk")', pad1)
        self.assertRegex(pad1, r"\(uuid \"[^\"]+\"\)")

    def test_pads_left_off_the_net_map_get_no_net_entry(self):
        pad3 = self.text[self.text.index('(pad "3"'):]
        self.assertNotIn("(net ", pad3)

    def test_geometry_is_copied_through_verbatim(self):
        self.assertIn('(fp_line', self.text)
        self.assertIn('(layer "F.CrtYd")', self.text)
@unittest.skipUnless(kicad.find_cli(), "未找到 kicad-cli")
class TestRealLibrary(unittest.TestCase):
    """真实 KiCad 库：拿数据手册事实校对，而不是拿实现校对。"""

    def test_library_root_is_found_next_to_the_cli(self):
        self.assertTrue((footprints.library_root() / "Package_DIP.pretty").is_dir())

    def test_dip14_matches_the_datasheet_pinout(self):
        fp = footprints.load("Package_DIP:DIP-14_W7.62mm")
        pos = fp.pad_positions((0.0, 0.0), 0)
        self.assertEqual(sorted(int(n) for n in pos), list(range(1, 15)))
        self.assertEqual(pos["1"], (0.0, 0.0))
        self.assertAlmostEqual(pos["2"][1] - pos["1"][1], 2.54)   # 脚距
        self.assertAlmostEqual(pos["14"][0] - pos["1"][0], 7.62)  # 行距
        self.assertEqual(pos["8"], (7.62, 15.24))                 # 右列自下往上

    def test_dip_pads_land_on_the_1_27mm_routing_grid(self):
        fp = footprints.load("Package_DIP:DIP-20_W7.62mm")
        for number, (x, y) in fp.pad_positions((0.0, 0.0), 0).items():
            for value in (x, y):
                self.assertAlmostEqual(value / 1.27, round(value / 1.27), places=6,
                                       msg=f"pad {number} 不在格点上")

    def test_every_footprint_the_board_uses_can_be_loaded(self):
        from hdc.pcb import cells, peripheral
        wanted = {v.format(n=2) if "{n" in v else v
                  for v in peripheral._F.values()}
        wanted |= {spec.footprint for spec in cells.CELLS.values()}
        wanted.add(cells.SCHMITT_INVERTER.footprint)
        for fp_id in sorted(wanted):
            with self.subTest(fp_id):
                fp = footprints.load(fp_id)
                self.assertTrue(fp.pads, f"{fp_id} 没有焊盘")

    def test_chip_footprints_expose_exactly_the_datasheet_pin_count(self):
        from hdc.pcb import cells
        for part, spec in sorted(cells.CELLS.items()):
            match = re.search(r"DIP-(\d+)", spec.footprint)
            if not match:
                continue
            with self.subTest(part):
                fp = footprints.load(spec.footprint)
                self.assertEqual(sorted(int(p.number) for p in fp.pads),
                                 list(range(1, int(match.group(1)) + 1)))


if __name__ == "__main__":
    unittest.main()

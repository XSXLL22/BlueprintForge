"""T1.6 制造文件导出与嘉立创格式转换单测。

格式转换是纯文本函数，所以这部分**不需要装 KiCad**：喂进去的样本是从真机 KiCad
10.0.6 的输出里原样抄来的（连 6 位小数、引号都没改），不是自己编的。

导出这一半需要 KiCad，判定标准刻意不去看实现：

* **坐标系**：拿钻孔文件里的孔坐标反过来校验 CPL —— 两者必须落在同一个坐标系。
  这条测试专门盯任务清单里那句「Mid Y 取反」：真按字面取反了，CPL 的 Y 全是正数
  而钻孔全是负数，这里立刻红。
* **板框闭合**（V3）：直接解析 Edge.Cuts 的 Gerber 命令，验证首尾相接，并把包围盒
  与 `plan.outline` 对上（Y 取负）。
* **产物完整**（V2）：`Fabrication.files` 里每个文件都存在且非空；ZIP 里正好是
  Gerber + 钻孔。
"""
import re
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from hdc.pcb import kicad, layout, manufacture, schematic
from hdc.pcb.manufacture import FabError, to_jlcpcb_bom, to_jlcpcb_cpl
from tests.test_pcb_layout import _counter_board

#: 从真机 `kicad-cli pcb export pos --format csv --units mm` 抄来的四行。
#: U1 的 −90 是把板文件改成 270° 之后回读到的；U2 的 bottom 是用 pcbnew 翻面后读的。
POS_CSV = """Ref,Val,Package,PosX,PosY,Rot,Side
"U1","74HC273","DIP-20_W7.62mm",16.510000,-17.780000,-90.000000,top
"U2","74HC283","DIP-16_W7.62mm",36.830000,-17.780000,-45.000000,bottom
"R1","100k","R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",86.360000,-24.130000,90.000000,top
"C1","100uF","CP_Radial_D6.3mm_P2.50mm",96.520000,-16.510000,0.000000,top
"""

#: 从真机 `kicad-cli sch export bom` 抄来的表头与三行。
BOM_CSV = """"Refs","Value","Footprint","Qty"
"C3,C4,C5","100nF","Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm","3"
"U1","74HC273","Package_DIP:DIP-20_W7.62mm","1"
"SW1","RESET","Button_Switch_THT:SW_PUSH_6mm","1"
"""


#: 嘉立创按扩展名认 Gerber 层（Protel 传统扩展名），与 KiCad 的层名拼写无关。
PROTEL_EXTENSIONS = {".gtl": "F.Cu", ".gbl": "B.Cu", ".gto": "F.SilkS",
                     ".gbo": "B.SilkS", ".gts": "F.Mask", ".gbs": "B.Mask",
                     ".gm1": "Edge.Cuts"}


def _rows(text: str) -> list[list[str]]:
    import csv
    import io
    return [r for r in csv.reader(io.StringIO(text)) if r]


class TestCplConversion(unittest.TestCase):
    def setUp(self):
        self.rows = _rows(to_jlcpcb_cpl(POS_CSV))
        self.by_ref = {row[0]: row for row in self.rows[1:]}

    def test_header_is_what_jlcpcb_asks_for(self):
        self.assertEqual(self.rows[0], ["Designator", "Val", "Package",
                                        "Mid X", "Mid Y", "Rotation", "Layer"])

    def test_every_component_survives_the_conversion(self):
        self.assertEqual(sorted(self.by_ref), ["C1", "R1", "U1", "U2"])

    def test_coordinates_keep_kicads_sign_and_gain_a_mm_suffix(self):
        """KiCad 的 pos 已经是 Gerber 坐标系（Y 朝上），数值一个字都不能改。"""
        self.assertEqual(self.by_ref["U1"][3], "16.5100mm")
        self.assertEqual(self.by_ref["U1"][4], "-17.7800mm")
        self.assertEqual(self.by_ref["C1"][4], "-16.5100mm")

    def test_rotation_is_normalised_into_zero_to_three_sixty(self):
        """KiCad 把 270° 回读成 −90；直接交给嘉立创就是装反 180°。"""
        self.assertEqual(self.by_ref["U1"][5], "270.00")   # −90
        self.assertEqual(self.by_ref["U2"][5], "315.00")   # −45
        self.assertEqual(self.by_ref["R1"][5], "90.00")
        self.assertEqual(self.by_ref["C1"][5], "0.00")

    def test_layer_names_are_capitalised(self):
        self.assertEqual(self.by_ref["U1"][6], "Top")
        self.assertEqual(self.by_ref["U2"][6], "Bottom")

    def test_value_and_package_pass_through(self):
        self.assertEqual(self.by_ref["U1"][1], "74HC273")
        self.assertEqual(self.by_ref["U1"][2], "DIP-20_W7.62mm")

    def test_an_unknown_side_is_refused_rather_than_guessed(self):
        broken = POS_CSV.replace(",top\n", ",middle\n", 1)
        with self.assertRaises(FabError) as ctx:
            to_jlcpcb_cpl(broken)
        self.assertIn("middle", str(ctx.exception))

    def test_a_missing_column_names_itself(self):
        broken = POS_CSV.replace("Rot,", "Angle,", 1)
        with self.assertRaises(FabError) as ctx:
            to_jlcpcb_cpl(broken)
        self.assertIn("Rot", str(ctx.exception))

    def test_a_non_numeric_coordinate_names_the_component(self):
        broken = POS_CSV.replace("16.510000,-17.780000", "left,-17.780000", 1)
        with self.assertRaises(FabError) as ctx:
            to_jlcpcb_cpl(broken)
        self.assertIn("U1", str(ctx.exception))

    def test_empty_input_is_reported_not_silently_accepted(self):
        with self.assertRaises(FabError):
            to_jlcpcb_cpl("")


class TestBomConversion(unittest.TestCase):
    def setUp(self):
        self.rows = _rows(to_jlcpcb_bom(BOM_CSV))
        self.by_ref = {row[1]: row for row in self.rows[1:]}

    def test_header_is_what_jlcpcb_asks_for(self):
        self.assertEqual(self.rows[0],
                         ["Comment", "Designator", "Footprint", "Quantity"])

    def test_value_becomes_the_comment_column(self):
        self.assertEqual(self.by_ref["U1"][0], "74HC273")

    def test_grouped_designators_stay_spelled_out(self):
        """嘉立创看不懂 `C3-C5` 这种区间缩写，必须逐个列出。"""
        self.assertIn("C3,C4,C5", self.by_ref)
        self.assertEqual(self.by_ref["C3,C4,C5"][3], "3")

    def test_the_library_prefix_is_stripped_off_the_footprint(self):
        self.assertEqual(self.by_ref["U1"][2], "DIP-20_W7.62mm")
        self.assertEqual(self.by_ref["SW1"][2], "SW_PUSH_6mm")

    def test_a_missing_column_names_itself(self):
        with self.assertRaises(FabError) as ctx:
            to_jlcpcb_bom(BOM_CSV.replace('"Qty"', '"Count"', 1))
        self.assertIn("Qty", str(ctx.exception))

    def test_empty_input_is_reported_not_silently_accepted(self):
        with self.assertRaises(FabError):
            to_jlcpcb_bom("")


# --- 需要真实 KiCad 的那一半 --------------------------------------------------

def _gerber_points(path: Path) -> list[tuple[float, float, str]]:
    """解析 Gerber 的画笔命令 → [(x, y, D01/D02)]。

    KiCad 写 `%MOMM*%` + `%FSLAX46Y46*%`：坐标是毫米，隐含 6 位小数。
    """
    out = []
    for x, y, op in re.findall(r"X(-?\d+)Y(-?\d+)(D0[12])", path.read_text(
            encoding="utf-8")):
        out.append((int(x) / 1e6, int(y) / 1e6, op))
    return out


def _drill_points(path: Path) -> list[tuple[float, float]]:
    text = path.read_text(encoding="utf-8")
    body = text[text.index("%"):] if "%" in text else text
    return [(float(x), float(y))
            for x, y in re.findall(r"^X(-?[\d.]+)Y(-?[\d.]+)\s*$", body,
                                   re.MULTILINE)]


@unittest.skipUnless(kicad.find_cli(), "未找到 kicad-cli（导出制造文件需要）")
class TestExport(unittest.TestCase):
    """整套导出跑一次 —— Gerber 与 PDF 都慢，别重复跑。"""

    @classmethod
    def setUpClass(cls):
        cls.board = _counter_board()
        cls.plan = layout.plan_layout(cls.board)
        cls._tmp = TemporaryDirectory()
        out = Path(cls._tmp.name)
        cls.sch = schematic.write_schematic(cls.board, out)
        cls.pcb = layout.write_pcb(cls.board, out)
        layout.fill_zones(cls.pcb)
        cls.fab = manufacture.export_fabrication(
            board=cls.pcb, schematic=cls.sch, out_dir=out)
        cls.out = out

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_every_promised_artifact_exists_and_is_not_empty(self):
        for path in self.fab.files:
            with self.subTest(path.name):
                self.assertTrue(path.is_file(), f"{path} 没生成")
                self.assertGreater(path.stat().st_size, 0, f"{path} 是空的")

    def test_all_seven_gerber_layers_are_there(self):
        """按扩展名认层 —— 嘉立创的上传器就是这么认的（Protel 扩展名）。"""
        got = {p.suffix.lower() for p in self.fab.gerbers
               if p.suffix.lower() != ".gbrjob"}
        self.assertEqual(got, set(PROTEL_EXTENSIONS),
                         f"缺层：{sorted(set(PROTEL_EXTENSIONS) - got)}")
        self.assertEqual(len(self.fab.gerbers), len(PROTEL_EXTENSIONS) + 1,
                         "多了或少了文件（应为 7 层 + 1 个 .gbrjob）")

    def test_the_zip_holds_exactly_the_gerbers_and_the_drill_file(self):
        with zipfile.ZipFile(self.fab.archive) as zf:
            entries = sorted(zf.namelist())
            self.assertEqual(entries, sorted([p.name for p in self.fab.gerbers]
                                             + [self.fab.drill.name]))
            self.assertFalse([n for n in entries if "/" in n], "ZIP 里有子目录")
            for info in zf.infolist():           # 时间戳写死 → 产物可复现
                self.assertEqual(info.date_time[:3], (1980, 1, 1))

    def test_the_drill_file_is_millimetres_and_absolute(self):
        text = self.fab.drill.read_text(encoding="utf-8")
        self.assertIn("METRIC", text)
        self.assertTrue(_drill_points(self.fab.drill), "钻孔文件里一个孔都没有")

    # --- 坐标系：CPL 必须与钻孔在同一个系里 ---------------------------------

    def _cpl(self) -> dict[str, tuple[float, float]]:
        rows = _rows(self.fab.cpl.read_text(encoding="utf-8"))
        head = rows[0]
        ix, iy = head.index("Mid X"), head.index("Mid Y")
        return {row[0]: (float(row[ix].removesuffix("mm")),
                         float(row[iy].removesuffix("mm"))) for row in rows[1:]}

    def test_dip_chips_sit_exactly_on_one_of_their_own_drill_holes(self):
        """DIP 封装的原点就是 1 脚，所以 CPL 坐标必须与某个孔逐位相同。

        这是「Mid Y 不能再取反」的硬证据：取反了就一个也对不上。
        """
        holes = {(round(x, 3), round(y, 3)) for x, y in
                 _drill_points(self.fab.drill)}
        checked = 0
        for ref, (x, y) in sorted(self._cpl().items()):
            if not ref.startswith("U"):
                continue
            checked += 1
            self.assertIn((round(x, 3), round(y, 3)), holes,
                          f"{ref} 的贴片坐标 {(x, y)} 不在任何孔上")
        self.assertGreaterEqual(checked, 2, "这块板没有 DIP 芯片？")

    def test_every_placement_falls_inside_the_drilled_area(self):
        holes = _drill_points(self.fab.drill)
        x1, x2 = min(h[0] for h in holes), max(h[0] for h in holes)
        y1, y2 = min(h[1] for h in holes), max(h[1] for h in holes)
        for ref, (x, y) in sorted(self._cpl().items()):
            with self.subTest(ref):
                self.assertTrue(x1 - 10 <= x <= x2 + 10, f"{ref} X 越界：{x}")
                self.assertTrue(y1 - 10 <= y <= y2 + 10, f"{ref} Y 越界：{y}")

    def test_the_cpl_lists_every_component_on_the_board(self):
        self.assertEqual(sorted(self._cpl()),
                         sorted(c.ref for c in self.board.components))

    def test_the_bom_covers_every_component_exactly_once(self):
        rows = _rows(self.fab.bom.read_text(encoding="utf-8"))[1:]
        refs = [ref for row in rows for ref in row[1].split(",") if ref]
        self.assertEqual(sorted(refs),
                         sorted(c.ref for c in self.board.components))
        for row in rows:
            with self.subTest(row[1]):
                self.assertEqual(int(row[3]), len(row[1].split(",")))

    # --- V3：板框闭合 --------------------------------------------------------

    def _edge_gerber(self) -> Path:
        found = [p for p in self.fab.gerbers if "Edge_Cuts" in p.name]
        self.assertEqual(len(found), 1, f"Edge.Cuts 层不唯一：{found}")
        return found[0]

    def test_every_gerber_layer_parses_as_rs274x(self):
        """看图软件报错的那几件事，在这里先自己查一遍。

        Gerber 是有限状态机：先声明坐标格式与单位，再定义光圈，然后才能选中它画
        图，最后以 `M02*` 收尾。选中一个没定义过的 `Dnn` 或者缺了格式声明，
        gerbv / KiCad 的 gerber viewer 打开就会报错 —— 那是 V3 真正要挡的错。
        """
        for path in sorted(self.fab.gerbers):
            if path.suffix.lower() == ".gbrjob":
                continue
            with self.subTest(path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("%FSLAX46Y46*%", text, "没声明坐标格式")
                self.assertIn("%MOMM*%", text, "没声明单位是毫米")
                self.assertEqual(text.rstrip().splitlines()[-1], "M02*",
                                 "文件没有以 M02* 收尾")
                defined = {int(n) for n in re.findall(r"%ADD(\d+)", text)}
                used = {int(n) for n in re.findall(r"^D(\d+)\*$", text,
                                                  re.MULTILINE)}
                self.assertTrue(used, "一个光圈都没选中过")
                self.assertFalse(used - defined,
                                 f"选中了没定义的光圈：{sorted(used - defined)}")

    def test_the_board_outline_gerber_is_a_closed_contour(self):
        """每个顶点的度数都是偶数 —— 笔画不管拆成几段，走一圈总能回到起点。"""
        pen, degree = None, {}
        for x, y, op in _gerber_points(self._edge_gerber()):
            point = (round(x, 3), round(y, 3))
            if op == "D01" and pen is not None and pen != point:
                degree[pen] = degree.get(pen, 0) + 1
                degree[point] = degree.get(point, 0) + 1
            pen = point
        self.assertTrue(degree, "Edge.Cuts 里一笔都没画")
        for point, count in sorted(degree.items()):
            self.assertEqual(count % 2, 0, f"板框在 {point} 断开（度数 {count}）")

    def test_the_outline_matches_the_planned_board_with_y_flipped(self):
        """Gerber 的 Y 轴朝上，板文件的 Y 轴朝下 —— 差一个负号，别的都得对上。"""
        points = [(x, y) for x, y, _ in _gerber_points(self._edge_gerber())]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x1, y1, x2, y2 = self.plan.outline
        self.assertAlmostEqual(min(xs), x1, places=2)
        self.assertAlmostEqual(max(xs), x2, places=2)
        self.assertAlmostEqual(min(ys), -y2, places=2)
        self.assertAlmostEqual(max(ys), -y1, places=2)

    # --- 收尾 ---------------------------------------------------------------

    def test_no_scratch_files_are_left_behind(self):
        left = sorted(p.name for p in self.out.iterdir()
                      if p.name.endswith("-kicad.csv"))
        self.assertEqual(left, [], f"临时文件没删干净：{left}")

    def test_the_archive_is_named_after_the_project(self):
        self.assertEqual(self.fab.archive.name,
                         f"{self.board.project}{manufacture.ARCHIVE_SUFFIX}")
        self.assertEqual(self.fab.project, self.board.project)

    def test_a_missing_input_file_is_reported_before_kicad_runs(self):
        with self.assertRaises(FabError) as ctx:
            manufacture.export_fabrication(
                board=self.out / "nope.kicad_pcb", schematic=self.sch,
                out_dir=self.out / "sub")
        self.assertIn("nope", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

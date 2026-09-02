"""T1.5（其二）布局单测。

判定标准尽量独立于实现：

* **摆放**：占地外框两两不重叠、全部落在板框内、去耦电容贴着它服务的那片 IC ——
  这些都是从 `footprints` 读出来的真实几何算的，不看 `layout` 的中间变量。
* **连通与短路**：直接复用布线器单测里那两个判定器（并查集连通分量、异网最小
  铜距离），喂进去的焊盘坐标由封装库 + 摆放结果重新算一遍。
* **文件格式**：把产出的 `.kicad_pcb` 解析回来对照；最后交给 `kicad-cli pcb drc`
  与 pcbnew 的铺铜器，让 KiCad 自己判卷。

摆放用到真实封装库（DIP 的孔径、电容的脚距都得是真的），所以整个文件在没装
KiCad 时跳过。
"""
import math
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hdc.pcb import footprints, kicad, layout, peripheral, router
from hdc.pcb.cells import spec_for
from hdc.pcb.pack import GND_NET, VCC_NET, Assembly, ChipInstance, IoPort, PinConn
from tests.test_pcb_router import _min_foreign_gap, _net_is_connected


def _counter_board() -> peripheral.Board:
    """与 T1.3/T1.4 同一块 4 位计数器板（2 片 IC + 完整外围）。"""
    reg, add = spec_for("74AC273_8x1DFFR"), spec_for("74AC283_1x1ADD4")
    asm = Assembly(project="counter")
    asm.chips = [ChipInstance("U1", reg, 4), ChipInstance("U2", add, 1)]
    asm.connections = [
        PinConn("U1", 20, VCC_NET, "VCC"), PinConn("U1", 10, GND_NET, "GND"),
        PinConn("U1", 11, "clk", "CLK"), PinConn("U1", 1, "rst_n", "C"),
        PinConn("U1", 3, "N9", "D"), PinConn("U1", 2, "count[0]", "Q", True),
        PinConn("U1", 4, "N10", "D"), PinConn("U1", 5, "count[1]", "Q", True),
        PinConn("U2", 16, VCC_NET, "VCC"), PinConn("U2", 8, GND_NET, "GND"),
        PinConn("U2", 5, "count[0]", "A0"), PinConn("U2", 3, "count[1]", "A1"),
        PinConn("U2", 6, VCC_NET, "B0"), PinConn("U2", 2, GND_NET, "B1"),
        PinConn("U2", 7, GND_NET, "CI"),
        PinConn("U2", 4, "N9", "S0", True), PinConn("U2", 1, "N10", "S1", True),
    ]
    asm.io = {
        "clk": IoPort("clk", "input", ["clk"]),
        "rst_n": IoPort("rst_n", "input", ["rst_n"]),
        "count": IoPort("count", "output", ["count[0]", "count[1]"]),
    }
    return peripheral.build_board(asm)


# --- 极小 s-expression 读取器（只用于测试，独立于实现） ----------------------

def _sexp(text: str):
    stack, cur = [], []
    for tok in re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text):
        if tok == "(":
            stack.append(cur)
            cur = []
        elif tok == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        elif tok.startswith('"'):
            cur.append(tok[1:-1].encode().decode("unicode_escape"))
        else:
            cur.append(tok)
    return cur[0]


def _kids(form, name):
    return [f for f in form if isinstance(f, list) and f and f[0] == name]


def _one(form, name):
    found = _kids(form, name)
    return found[0] if found else None


def _xy(form, name) -> tuple[float, float]:
    got = _one(form, name)
    return (float(got[1]), float(got[2]))


# --- 从「封装库 + 摆放结果」重新算出每个焊盘 ---------------------------------

def _pads(board: peripheral.Board, plan: "layout.Layout") -> list[router.Pad]:
    """所有焊盘的绝对坐标与网络。没接线的脚给一个独一无二的假网络名 ——
    它们不该被布线，但必须当障碍物看待。"""
    out = []
    for place in plan.placements:
        comp = board.by_ref(place.ref)
        fp = footprints.load(place.footprint)
        spot = fp.pad_positions((place.x, place.y), place.rotation)
        for pad in fp.pads:
            net = comp.pins.get(int(pad.number), "") if pad.number.isdigit() else ""
            x, y = spot[pad.number]
            out.append(router.Pad(net or f"\x01{place.ref}.{pad.number}",
                                  x, y, pad.radius, pad.layers))
    return out


def _boxes(plan: "layout.Layout") -> dict[str, tuple[float, float, float, float]]:
    return {p.ref: footprints.load(p.footprint).placed_bbox((p.x, p.y), p.rotation)
            for p in plan.placements}


def _overlap(a, b) -> bool:
    return (a[0] < b[2] - 1e-9 and b[0] < a[2] - 1e-9
            and a[1] < b[3] - 1e-9 and b[1] < a[3] - 1e-9)


@unittest.skipUnless(kicad.find_cli(), "未找到 kicad-cli（布局需要真实封装库）")
class _Planned(unittest.TestCase):
    """整块板只规划一次 —— 布线是这套测试里最慢的一步。"""

    @classmethod
    def setUpClass(cls):
        cls.board = _counter_board()
        cls.plan = layout.plan_layout(cls.board)


class TestPlacement(_Planned):
    def test_every_component_is_placed_exactly_once(self):
        placed = sorted(p.ref for p in self.plan.placements)
        self.assertEqual(placed, sorted(c.ref for c in self.board.components))

    def test_footprint_and_value_come_from_the_board(self):
        for place in self.plan.placements:
            comp = self.board.by_ref(place.ref)
            self.assertEqual(place.footprint, comp.footprint)
            self.assertEqual(place.value, comp.value)

    def test_courtyards_do_not_overlap(self):
        boxes = sorted(_boxes(self.plan).items())
        for i, (ref_a, box_a) in enumerate(boxes):
            for ref_b, box_b in boxes[i + 1:]:
                self.assertFalse(_overlap(box_a, box_b),
                                 f"{ref_a} 与 {ref_b} 的占地重叠：{box_a} {box_b}")

    def test_everything_sits_inside_the_board_outline(self):
        x1, y1, x2, y2 = self.plan.outline
        for ref, (bx1, by1, bx2, by2) in sorted(_boxes(self.plan).items()):
            self.assertTrue(x1 <= bx1 and bx2 <= x2 and y1 <= by1 and by2 <= y2,
                            f"{ref} 探出板框：{(bx1, by1, bx2, by2)}")

    def test_decoupling_caps_land_next_to_their_chip(self):
        """去耦电容离它服务的那片 IC 必须够近，否则去耦没有意义。"""
        boxes = _boxes(self.plan)
        spot = {p.ref: (p.x, p.y) for p in self.plan.placements}
        checked = 0
        for comp in self.board.components:
            if comp.kind != "cap_decoupling":
                continue
            checked += 1
            gap = math.dist(spot[comp.ref], spot[comp.near])
            span = max(boxes[comp.near][2] - boxes[comp.near][0],
                       boxes[comp.near][3] - boxes[comp.near][1])
            self.assertLess(gap, span + 15.0,
                            f"{comp.ref} 离 {comp.near} 有 {gap:.1f}mm")
        self.assertTrue(checked, "这块板没有去耦电容，测试白跑了")

    def test_dip_chips_keep_their_long_axis_vertical(self):
        """DIP 竖放（0°）：引脚列沿 Y 排开，走线主要在水平方向穿行。"""
        for place in self.plan.placements:
            if "DIP-" not in place.footprint:
                continue
            self.assertEqual(place.rotation % 180, 0, f"{place.ref} 横躺着")

    def test_board_stays_within_one_jlcpcb_panel(self):
        x1, y1, x2, y2 = self.plan.outline
        self.assertLess(x2 - x1, 200.0, "板子太宽")
        self.assertLess(y2 - y1, 200.0, "板子太高")

    def test_planning_is_deterministic(self):
        again = layout.plan_layout(self.board)
        self.assertEqual(again.placements, self.plan.placements)
        self.assertEqual(again.segments, self.plan.segments)
        self.assertEqual(again.vias, self.plan.vias)


class TestNets(_Planned):
    def test_every_board_net_gets_a_code(self):
        self.assertEqual(sorted(self.plan.nets), sorted(self.board.nets))

    def test_net_codes_are_dense_and_start_at_one(self):
        codes = sorted(self.plan.code_of(n) for n in self.plan.nets)
        self.assertEqual(codes, list(range(1, len(self.plan.nets) + 1)))

    def test_the_unconnected_code_is_reserved_for_no_net(self):
        self.assertEqual(self.plan.code_of(""), 0)


class TestRouting(_Planned):
    def test_nothing_is_left_unrouted(self):
        self.assertEqual(list(self.plan.unrouted), [])

    def test_every_signal_net_is_connected(self):
        """连通性由并查集独立判定，不看布线器说了什么。"""
        pads = _pads(self.board, self.plan)
        result = router.RouteResult(list(self.plan.segments), list(self.plan.vias))
        multi = {p.net for p in pads if [q for q in pads if q.net == p.net][1:]}
        checked = 0
        for net in sorted(multi - {GND_NET}):
            if net.startswith("\x01"):
                continue
            checked += 1
            self.assertTrue(_net_is_connected(result, pads, net), f"网络 {net} 断了")
        self.assertGreater(checked, 5, "这块板的网络太少，测试没有说服力")

    def test_no_foreign_net_clearance_violation(self):
        pads = _pads(self.board, self.plan)
        result = router.RouteResult(list(self.plan.segments), list(self.plan.vias))
        opts = self.plan.options
        gap = _min_foreign_gap(result, pads, opts)
        self.assertGreaterEqual(gap, opts.clearance - 1e-9,
                                f"最小异网间距 {gap:.3f}mm < {opts.clearance}mm")

    def test_gnd_is_left_to_the_copper_zone(self):
        self.assertFalse([s for s in self.plan.segments if s.net == GND_NET])


class TestFileStructure(_Planned):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.text = layout.render(cls.plan)
        cls.doc = _sexp(cls.text)

    def test_output_is_a_balanced_kicad_pcb(self):
        depth = 0
        for tok in re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"', self.text):
            depth += 1 if tok == "(" else -1 if tok == ")" else 0
            self.assertGreaterEqual(depth, 0)
        self.assertEqual(depth, 0)
        self.assertEqual(self.doc[0], "kicad_pcb")

    def test_all_layers_used_anywhere_are_declared(self):
        """引用了没声明的层，KiCad 打开时直接报错 —— 提前在这里挡住。"""
        declared = {row[1] for row in _one(self.doc, "layers")[1:]
                    if isinstance(row, list)}
        wild = {"*.Cu", "*.Mask", "*.Paste", "*.SilkS", "F&B.Cu"}
        used = set(re.findall(r'\((?:layer|layers) ((?:"[^"]+"\s*)+)\)', self.text))
        names = {n for group in used for n in re.findall(r'"([^"]+)"', group)}
        self.assertTrue(names, "文件里一个层都没用到？")
        self.assertFalse(names - declared - wild,
                         f"用到但没声明的层：{sorted(names - declared - wild)}")

    def test_one_footprint_section_per_component_at_its_planned_position(self):
        spots = {}
        for form in _kids(self.doc, "footprint"):
            ref = next(p[2] for p in _kids(form, "property") if p[1] == "Reference")
            spots[ref] = (form[1], *_xy(form, "at"))
        self.assertEqual(sorted(spots), sorted(p.ref for p in self.plan.placements))
        for place in self.plan.placements:
            self.assertEqual(spots[place.ref],
                             (place.footprint, place.x, place.y))

    def test_pads_carry_the_net_codes_from_the_layout(self):
        seen = 0
        for form in _kids(self.doc, "footprint"):
            ref = next(p[2] for p in _kids(form, "property") if p[1] == "Reference")
            comp = self.board.by_ref(ref)
            for pad in _kids(form, "pad"):
                net = _one(pad, "net")
                if net is None:
                    self.assertNotIn(int(pad[1]), comp.pins)
                    continue
                seen += 1
                self.assertEqual(net[2], comp.pins[int(pad[1])])
                self.assertEqual(int(net[1]), self.plan.code_of(net[2]))
        self.assertGreater(seen, 20, "焊盘上的网络太少，多半没写进去")

    def test_edge_cuts_form_a_closed_rectangle(self):
        edges = [f for f in _kids(self.doc, "gr_line")
                 if _one(f, "layer")[1] == "Edge.Cuts"]
        self.assertEqual(len(edges), 4)
        ends = []
        for line in edges:
            ends += [_xy(line, "start"), _xy(line, "end")]
        for point in set(ends):                 # 闭合 → 每个角出现两次
            self.assertEqual(ends.count(point), 2, f"板框在 {point} 断开")
        x1, y1, x2, y2 = self.plan.outline
        self.assertEqual(set(ends), {(x1, y1), (x2, y1), (x2, y2), (x1, y2)})

    def test_tracks_and_vias_are_all_written(self):
        self.assertEqual(len(_kids(self.doc, "segment")), len(self.plan.segments))
        self.assertEqual(len(_kids(self.doc, "via")), len(self.plan.vias))
        for form in _kids(self.doc, "segment"):
            self.assertIn(_one(form, "layer")[1], ("F.Cu", "B.Cu"))
            self.assertEqual(float(_one(form, "width")[1]),
                             self.plan.options.track_width)

    def test_gnd_zone_covers_the_board_on_the_back_layer(self):
        zones = _kids(self.doc, "zone")
        self.assertEqual(len(zones), 1)
        zone = zones[0]
        self.assertEqual(_one(zone, "net_name")[1], GND_NET)
        self.assertEqual(int(_one(zone, "net")[1]), self.plan.code_of(GND_NET))
        self.assertEqual([n for n in _one(zone, "layers")[1:]], ["B.Cu"])
        points = [(float(p[1]), float(p[2]))
                  for p in _kids(_one(_one(zone, "polygon"), "pts"), "xy")]
        x1, y1, x2, y2 = self.plan.outline
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self.assertGreaterEqual(min(xs), x1)
        self.assertLessEqual(max(xs), x2)
        self.assertGreaterEqual(min(ys), y1)
        self.assertLessEqual(max(ys), y2)
        self.assertGreater((max(xs) - min(xs)) * (max(ys) - min(ys)),
                           0.5 * (x2 - x1) * (y2 - y1), "铺铜面积太小")


class TestKicadAccepts(_Planned):
    """让 KiCad 自己判卷：能打开、能铺铜、DRC 干净。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = TemporaryDirectory()
        cls.path = layout.write_pcb(cls.board, Path(cls._tmp.name))
        cls.fill = layout.fill_zones(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_board_file_lands_next_to_the_project_name(self):
        self.assertEqual(self.path.name, f"{self.board.project}.kicad_pcb")
        self.assertGreater(self.path.stat().st_size, 10_000)

    def test_kicad_cli_can_read_the_board_back(self):
        out = self.path.with_suffix(".pdf")
        proc = kicad.run(["pcb", "export", "pdf", "--layers", "F.Cu,Edge.Cuts",
                          "-o", out, self.path])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(out.is_file())

    def test_filling_the_gnd_zone_covers_real_area(self):
        x1, y1, x2, y2 = self.plan.outline
        self.assertGreater(self.fill.area, 0.3 * (x2 - x1) * (y2 - y1),
                           f"铺铜只盖住 {self.fill.area:.0f}mm²")

    def test_the_ground_plane_is_one_single_piece(self):
        """地平面必须是一整块。

        KiCad 会把「没有焊盘落在上面」的碎铜自动删掉，所以数出来的每一块都挂着
        焊盘 —— 大于 1 就意味着某些 GND 焊盘的地只连到一座孤岛上（信号在底层长途
        奔袭时会围出这种孤岛），DRC 随后会报 `unconnected_items`。
        """
        self.assertEqual(self.fill.islands, 1,
                         f"地平面被切成 {self.fill.islands} 块")

    def test_drc_reports_no_violations(self):
        report = self.path.with_name("drc.rpt")
        proc = kicad.run(["pcb", "drc", "--exit-code-violations",
                          "--severity-error", "-o", report, self.path])
        text = report.read_text(encoding="utf-8") if report.is_file() else ""
        self.assertEqual(proc.returncode, 0,
                         f"DRC 报错：\n{text}\n{proc.stdout}\n{proc.stderr}")


if __name__ == "__main__":
    unittest.main()

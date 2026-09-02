"""T1.4 原理图单测。

分两层：

1. **纯结构层**：把渲染出的 `.kicad_sch` 当 s-expression 解析回来，检查元件、
   引脚、位置、连接关系是否与 `Board` 一致 —— 不需要装 KiCad。
2. **真实工具层**：调 `kicad-cli` 导出网表 / PDF / BOM。网表是连接关系的
   **权威事实来源**（由 KiCad 自己的连线引擎算出），用来验证坐标变换与标签
   放置真的对；没装 KiCad 就跳过。
"""
import csv
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hdc.pcb import kicad, peripheral, schematic
from hdc.pcb.pack import GND_NET, VCC_NET, Assembly, ChipInstance, IoPort, PinConn
from hdc.pcb.cells import spec_for


def _counter_board() -> peripheral.Board:
    """与 T1.3 相同的 4 位计数器精简装箱结果，补成完整板子。"""
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


# --- 极小 s-expression 读取器（只用于测试，独立于实现） --------------------

def _sexp(text: str):
    tokens = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text)
    stack, cur = [], []
    for tok in tokens:
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


def _prop(sym, key):
    for p in _kids(sym, "property"):
        if p[1] == key:
            return p[2]
    return None
def _lib_pins(doc) -> dict[str, dict[str, tuple[float, float]]]:
    """`lib_symbols` 里每个符号的「引脚号 → 库坐标」。"""
    out = {}
    for sym in _kids(_one(doc, "lib_symbols"), "symbol"):
        pins = {}
        for unit in _kids(sym, "symbol"):
            for pin in _kids(unit, "pin"):
                at = _one(pin, "at")
                pins[_one(pin, "number")[1]] = (float(at[1]), float(at[2]))
        out[sym[1]] = pins
    return out


def _placed_pins(doc) -> dict[tuple[float, float], tuple[str, int]]:
    """每个已放置符号的引脚在图纸上的坐标 → (位号, 引脚号)。

    库坐标 Y 轴朝上、图纸 Y 轴朝下，所以是 `sheet = (ox + lx, oy - ly)`。
    """
    libs, out = _lib_pins(doc), {}
    for sym in _kids(doc, "symbol"):
        at = _one(sym, "at")
        ox, oy = float(at[1]), float(at[2])
        ref = _prop(sym, "Reference")
        for num, (lx, ly) in libs[_one(sym, "lib_id")[1]].items():
            out[(round(ox + lx, 3), round(oy - ly, 3))] = (ref, int(num))
    return out


def _net_map(text: str) -> dict[str, set[tuple[str, int]]]:
    """按几何关系还原「网络 → {(位号, 引脚号)}」。

    约定：每个引脚伸出一段导线，另一端放一个网络名标签。
    """
    doc = _sexp(text)
    pin_at = _placed_pins(doc)
    labels = {}
    for kind in ("label", "global_label"):
        for lab in _kids(doc, kind):
            at = _one(lab, "at")
            labels[(round(float(at[1]), 3), round(float(at[2]), 3))] = lab[1]

    nets: dict[str, set[tuple[str, int]]] = {}
    for wire in _kids(doc, "wire"):
        (_, x1, y1), (_, x2, y2) = _kids(_one(wire, "pts"), "xy")
        a = (round(float(x1), 3), round(float(y1), 3))
        b = (round(float(x2), 3), round(float(y2), 3))
        for end, other in ((a, b), (b, a)):
            if end in labels and other in pin_at:
                nets.setdefault(labels[end], set()).add(pin_at[other])
    return nets


def _board_net_map(board) -> dict[str, set[tuple[str, int]]]:
    out: dict[str, set[tuple[str, int]]] = {}
    for comp in board.components:
        for pin, net in comp.pins.items():
            out.setdefault(net, set()).add((comp.ref, pin))
    return out


def _pins_in_package(comp) -> int:
    """封装名给出的物理引脚数（独立于实现的事实来源）。"""
    m = re.search(r"DIP-(\d+)", comp.footprint) or re.search(r"1x(\d+)",
                                                             comp.footprint)
    return int(m.group(1)) if m else max(comp.pins)

class TestFileStructure(unittest.TestCase):
    def setUp(self):
        self.board = _counter_board()
        self.text = schematic.render(self.board)
        self.doc = _sexp(self.text)

    def test_top_level_form_is_a_kicad_schematic(self):
        self.assertEqual(self.doc[0], "kicad_sch")
        for key in ("version", "generator", "uuid", "paper", "lib_symbols"):
            self.assertIsNotNone(_one(self.doc, key), f"缺少 {key}")

    def test_version_is_a_kicad_date_stamp(self):
        self.assertRegex(_one(self.doc, "version")[1], r"^20\d{6}$")

    def test_every_placed_symbol_resolves_to_an_embedded_definition(self):
        defined = set(_lib_pins(self.doc))
        for sym in _kids(self.doc, "symbol"):
            self.assertIn(_one(sym, "lib_id")[1], defined)

    def test_every_component_is_placed_exactly_once(self):
        placed = [_prop(s, "Reference") for s in _kids(self.doc, "symbol")]
        self.assertEqual(sorted(placed),
                         sorted(c.ref for c in self.board.components))

    def test_placed_symbols_carry_value_and_footprint(self):
        by_ref = {_prop(s, "Reference"): s for s in _kids(self.doc, "symbol")}
        for comp in self.board.components:
            sym = by_ref[comp.ref]
            self.assertEqual(_prop(sym, "Value"), comp.value, comp.ref)
            self.assertEqual(_prop(sym, "Footprint"), comp.footprint, comp.ref)

    def test_symbol_instance_path_matches_the_sheet_uuid(self):
        root = _one(self.doc, "uuid")[1]
        for sym in _kids(self.doc, "symbol"):
            path = _one(_one(_one(sym, "instances"), "project"), "path")[1]
            self.assertEqual(path, f"/{root}")

    def test_render_is_deterministic(self):
        self.assertEqual(self.text, schematic.render(_counter_board()))

class TestSymbolGeneration(unittest.TestCase):
    def setUp(self):
        self.board = _counter_board()
        self.doc = _sexp(schematic.render(self.board))
        self.libs = _lib_pins(self.doc)
        self.by_ref = {_prop(s, "Reference"): s for s in _kids(self.doc, "symbol")}

    def test_symbol_exposes_every_wired_pin(self):
        for comp in self.board.components:
            lib = self.libs[_one(self.by_ref[comp.ref], "lib_id")[1]]
            missing = {p for p in comp.pins if str(p) not in lib}
            self.assertFalse(missing, f"{comp.ref} 符号缺少引脚 {sorted(missing)}")

    def test_dip_symbols_expose_the_whole_package(self):
        """IC 符号必须画出整片的引脚 —— 封装名里的 DIP-N 是独立事实来源。"""
        for comp in self.board.components:
            m = re.search(r"DIP-(\d+)", comp.footprint)
            if not m:
                continue
            lib = self.libs[_one(self.by_ref[comp.ref], "lib_id")[1]]
            self.assertEqual(sorted(int(p) for p in lib),
                             list(range(1, int(m.group(1)) + 1)), comp.ref)

    def test_identical_chips_share_one_symbol_definition(self):
        """同型号只生成一个符号定义，否则 PDF 与 BOM 都会膨胀。"""
        ids = {_one(s, "lib_id")[1] for s in _kids(self.doc, "symbol")}
        values = {c.value for c in self.board.components}
        self.assertLessEqual(len(ids), len(values) + 1)

    def test_no_two_pins_land_on_the_same_point(self):
        """两个引脚重合会被 KiCad 判为短路 —— 布图必须避免。"""
        seen = {}
        for sym in _kids(self.doc, "symbol"):
            at = _one(sym, "at")
            ox, oy = float(at[1]), float(at[2])
            ref = _prop(sym, "Reference")
            for num, (lx, ly) in self.libs[_one(sym, "lib_id")[1]].items():
                key = (round(ox + lx, 3), round(oy - ly, 3))
                self.assertNotIn(key, seen,
                                 f"{ref}.{num} 与 {seen.get(key)} 引脚重合于 {key}")
                seen[key] = f"{ref}.{num}"

    def test_symbol_pin_areas_do_not_overlap(self):
        boxes = {}
        for sym in _kids(self.doc, "symbol"):
            at = _one(sym, "at")
            ox, oy = float(at[1]), float(at[2])
            pts = [(ox + lx, oy - ly)
                   for lx, ly in self.libs[_one(sym, "lib_id")[1]].values()]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            boxes[_prop(sym, "Reference")] = (min(xs), min(ys), max(xs), max(ys))
        items = sorted(boxes.items())
        for i, (ref_a, a) in enumerate(items):
            for ref_b, b in items[i + 1:]:
                overlap = (a[0] < b[2] and b[0] < a[2]
                           and a[1] < b[3] and b[1] < a[3])
                self.assertFalse(overlap, f"{ref_a} 与 {ref_b} 图形重叠：{a} {b}")

class TestConnectivity(unittest.TestCase):
    def test_geometry_reproduces_the_board_net_map(self):
        board = _counter_board()
        self.assertEqual(_net_map(schematic.render(board)), _board_net_map(board))

    def test_writes_a_file_named_after_the_project(self):
        board = _counter_board()
        with TemporaryDirectory() as tmp:
            path = schematic.write_schematic(board, Path(tmp))
            self.assertEqual(path.name, "counter.kicad_sch")
            self.assertEqual(path.read_text(encoding="utf-8"),
                             schematic.render(board))


@unittest.skipUnless(kicad.find_cli(), "未找到 kicad-cli")
class TestKicadAgrees(unittest.TestCase):
    """用 KiCad 自己的连线引擎与导出器验收 —— 这是权威事实来源。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        cls.board = _counter_board()
        cls.sch = schematic.write_schematic(cls.board, cls.dir)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_kicad_netlist_matches_the_board(self):
        out = self.dir / "counter.net"
        kicad.run(["sch", "export", "netlist", "--format", "kicadsexpr",
                   "-o", out, self.sch], check=True)
        doc = _sexp(out.read_text(encoding="utf-8", errors="replace"))
        got: dict[str, set[tuple[str, int]]] = {}
        for net in _kids(_one(doc, "nets"), "net"):
            name = _one(net, "name")[1].lstrip("/")
            for node in _kids(net, "node"):
                got.setdefault(name, set()).add(
                    (_one(node, "ref")[1], int(_one(node, "pin")[1])))
        named = {n: p for n, p in got.items() if not n.startswith("unconnected-")}
        self.assertEqual(named, _board_net_map(self.board))

    def test_only_deliberately_unwired_pins_are_left_floating(self):
        """KiCad 报的 unconnected 引脚必须恰好是 Board 里没接线的那些。"""
        out = self.dir / "counter.net"
        kicad.run(["sch", "export", "netlist", "--format", "kicadsexpr",
                   "-o", out, self.sch], check=True)
        doc = _sexp(out.read_text(encoding="utf-8", errors="replace"))
        floating = {(_one(node, "ref")[1], int(_one(node, "pin")[1]))
                    for net in _kids(_one(doc, "nets"), "net")
                    if _one(net, "name")[1].startswith("unconnected-")
                    for node in _kids(net, "node")}
        expected = {(c.ref, p) for c in self.board.components
                    for p in range(1, _pins_in_package(c) + 1)
                    if p not in c.pins}
        self.assertEqual(floating, expected)

    def test_pdf_export_produces_a_readable_document(self):
        out = self.dir / "counter.pdf"
        kicad.run(["sch", "export", "pdf", "-o", out, self.sch], check=True)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 5_000)
        self.assertTrue(out.read_bytes().startswith(b"%PDF"))

    def test_bom_lists_every_component(self):
        out = self.dir / "counter_bom.csv"
        kicad.run(["sch", "export", "bom", "--fields", "Reference,Value,Footprint",
                   "--labels", "Reference,Value,Footprint", "--group-by", "",
                   "--ref-range-delimiter", "", "-o", out, self.sch], check=True)
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        refs = {r for row in rows for r in row["Reference"].split(",")}
        self.assertEqual(refs, {c.ref for c in self.board.components})

    def test_erc_reports_no_errors(self):
        out = self.dir / "counter_erc.json"
        kicad.run(["sch", "erc", "--severity-error", "--format", "json",
                   "-o", out, self.sch])
        import json
        report = json.loads(out.read_text(encoding="utf-8"))
        problems = [v for sheet in report.get("sheets", [])
                    for v in sheet.get("violations", [])
                    if v.get("severity") == "error"]
        self.assertFalse(problems, [p.get("description") for p in problems])


if __name__ == "__main__":
    unittest.main()

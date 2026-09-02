"""74 系列芯片知识库单测。

关键点：期望值不来自 `cells.py` 自身，而来自 vendor 的 Liberty 库 / Verilog 模型
（独立事实来源），用于交叉验证手写引脚表的端口名与覆盖度。
"""
import re
import unittest
from pathlib import Path

from hdc.pcb import cells

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "74xx-liberty"


def _liberty_cells() -> dict[str, dict[str, str]]:
    """从 74ac.lib 解析 {cell 名: {pin 名: direction}}（独立事实来源）。"""
    text = (VENDOR / "74ac.lib").read_text(encoding="utf-8", errors="replace")
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(r'cell\(\s*"?([\w$]+)"?\s*\)\s*\{', text):
        name = m.group(1)
        # 取该 cell 块正文：从 '{' 起做括号配平
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = text[m.end():i]
        pins = {}
        for pm in re.finditer(r"pin\(\s*(\w+)\s*\)\s*\{([^}]*)\}", body):
            d = "output" if "direction: output" in pm.group(2) else "input"
            pins[pm.group(1)] = d
        out[name] = pins
    return out


def _model_modules() -> dict[str, list[str]]:
    """从 74_models.v 解析 {模块名: [端口名...]}（独立事实来源）。"""
    text = (VENDOR / "74_models.v").read_text(encoding="utf-8", errors="replace")
    out = {}
    for m in re.finditer(r"module\s+\\?([\w$]+)\s*\(([^)]*)\)", text):
        out[m.group(1)] = [p.strip() for p in m.group(2).split(",") if p.strip()]
    return out


def _model_outputs() -> dict[str, set[str]]:
    """从 74_models.v 的 `output` 声明解析每个模块的输出端口（独立事实来源）。"""
    text = (VENDOR / "74_models.v").read_text(encoding="utf-8", errors="replace")
    out: dict[str, set[str]] = {}
    for block in re.split(r"\bmodule\s+", text)[1:]:
        name = re.match(r"\\?([\w$]+)", block).group(1)
        names: set[str] = set()
        for om in re.finditer(r"^\s*output\s+(?:reg\s+)?(?:\[[^\]]*\]\s*)?([^;]+);",
                              block, re.M):
            names.update(p.strip() for p in om.group(1).split(","))
        out[name] = names
    return out


class TestCoverage(unittest.TestCase):
    def test_every_liberty_cell_has_a_chip_spec(self):
        for name in _liberty_cells():
            if name == "$_BUF_":
                continue  # 缓冲器在装箱阶段退化为网络别名，不占芯片
            self.assertIn(name, cells.CELLS, f"Liberty cell {name} 缺少芯片规格")

    def test_every_model_module_has_a_chip_spec(self):
        for name in _model_modules():
            self.assertIn(name, cells.CELLS, f"模型模块 {name} 缺少芯片规格")

    def test_buffer_is_declared_as_net_alias(self):
        self.assertTrue(cells.is_net_alias("$_BUF_"))
        self.assertFalse(cells.is_net_alias("74AC00_4x1NAND2"))


class TestPortNamesMatchVendor(unittest.TestCase):
    def test_liberty_pin_names_match_slot_and_shared_ports(self):
        for name, pins in _liberty_cells().items():
            if name == "$_BUF_":
                continue
            spec = cells.CELLS[name]
            declared = set(spec.slots[0]) | set(spec.shared)
            self.assertEqual(
                declared, set(pins),
                f"{name} 端口集合与 Liberty 不一致：表={sorted(declared)} lib={sorted(pins)}",
            )

    def test_model_ports_match_slot_and_shared_ports(self):
        for name, ports in _model_modules().items():
            spec = cells.CELLS[name]
            # 表里总线端口按位展开（A0/A1/...），归一化回总线名再比对
            declared = {re.sub(r"\d+$", "", p) for p in (set(spec.slots[0]) | set(spec.shared))}
            self.assertEqual(
                declared, set(ports),
                f"{name} 端口集合与模型不一致：表={sorted(declared)} model={sorted(ports)}",
            )


class TestPortDirectionsMatchVendor(unittest.TestCase):
    """`ChipSpec.outputs` 决定未用槽位是否接地，必须与 vendor 的方向声明一致。"""

    def test_liberty_output_pins_match_outputs_field(self):
        for name, pins in _liberty_cells().items():
            if name == "$_BUF_":
                continue
            spec = cells.CELLS[name]
            expected = {p for p, d in pins.items() if d == "output"}
            declared = {p for p in (set(spec.slots[0]) | set(spec.shared))
                        if spec.is_output(p)}
            self.assertEqual(declared, expected, f"{name} 输出端口集合与 Liberty 不一致")

    def test_model_output_ports_match_outputs_field(self):
        outs = _model_outputs()
        for name, ports in _model_modules().items():
            spec = cells.CELLS[name]
            declared = {re.sub(r"\d+$", "", p)
                        for p in (set(spec.slots[0]) | set(spec.shared))
                        if spec.is_output(p)}
            self.assertEqual(declared, outs[name], f"{name} 输出端口集合与模型不一致")

    def test_inputs_are_not_reported_as_outputs(self):
        spec = cells.spec_for("74AC273_8x1DFFR")
        self.assertTrue(spec.is_output("Q"))
        self.assertFalse(spec.is_output("D"))
        self.assertFalse(spec.is_output("CLK"))


class TestPinTableIntegrity(unittest.TestCase):
    def test_pins_are_unique_and_in_range(self):
        for name, spec in cells.CELLS.items():
            used: dict[int, str] = {}
            def claim(pin: int, who: str, _used=used, _name=name, _spec=spec):
                self.assertIn(pin, range(1, _spec.pin_count + 1),
                              f"{_name}.{who} 引脚 {pin} 超出 1..{_spec.pin_count}")
                self.assertNotIn(pin, _used,
                                 f"{_name} 引脚 {pin} 被 {_used.get(pin)} 与 {who} 重复占用")
                _used[pin] = who

            claim(spec.vcc, "VCC")
            claim(spec.gnd, "GND")
            for pin, level in spec.tie.items():
                claim(pin, f"tie->{level}")
            for i, slot in enumerate(spec.slots):
                for port, pin in slot.items():
                    claim(pin, f"slot{i}.{port}")
            for port, pin in spec.shared.items():
                claim(pin, f"shared.{port}")

    def test_all_slots_have_same_port_set(self):
        for name, spec in cells.CELLS.items():
            first = set(spec.slots[0])
            for i, slot in enumerate(spec.slots[1:], start=1):
                self.assertEqual(set(slot), first, f"{name} slot{i} 端口集合与 slot0 不一致")

    def test_slot_count_matches_cell_name(self):
        """Liberty/模型命名 `<part>_<N>x<M><FUNC>`：N 为片内槽位数。"""
        for name, spec in cells.CELLS.items():
            m = re.search(r"_(\d+)x", name)
            self.assertIsNotNone(m, f"{name} 命名不含槽位数")
            self.assertEqual(int(m.group(1)), len(spec.slots),
                             f"{name} 槽位数应为 {m.group(1)}，实际 {len(spec.slots)}")

    def test_footprint_matches_pin_count(self):
        for name, spec in cells.CELLS.items():
            self.assertIn(f"DIP-{spec.pin_count}_", spec.footprint,
                          f"{name} 封装 {spec.footprint} 与 {spec.pin_count} 引脚不匹配")


class TestLookup(unittest.TestCase):
    def test_nand2_spec(self):
        spec = cells.spec_for("74AC00_4x1NAND2")
        self.assertEqual(spec.part, "74HC00")
        self.assertEqual(spec.pin_count, 14)
        self.assertEqual((spec.vcc, spec.gnd), (14, 7))
        self.assertEqual(len(spec.slots), 4)
        self.assertEqual(spec.slots[0], {"A": 1, "B": 2, "Y": 3})
        self.assertEqual(spec.slots[3], {"A": 12, "B": 13, "Y": 11})
        self.assertEqual(spec.shared, {})

    def test_dffr_shares_clock_and_reset(self):
        spec = cells.spec_for("74AC273_8x1DFFR")
        self.assertEqual(spec.part, "74HC273")
        self.assertEqual(len(spec.slots), 8)
        self.assertEqual(spec.shared, {"CLK": 11, "C": 1})
        self.assertEqual(spec.slots[0], {"D": 3, "Q": 2})

    def test_bus_ports_are_bit_indexed(self):
        spec = cells.spec_for("74AC283_1x1ADD4")
        self.assertEqual(spec.part, "74HC283")
        self.assertEqual(len(spec.slots), 1)
        self.assertEqual(spec.slots[0]["A0"], 5)
        self.assertEqual(spec.slots[0]["S3"], 10)
        self.assertEqual(spec.slots[0]["CI"], 7)

    def test_unknown_cell_raises_with_actionable_message(self):
        with self.assertRaises(cells.UnmappedCellError) as ctx:
            cells.spec_for("$_NAND_")
        self.assertIn("$_NAND_", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

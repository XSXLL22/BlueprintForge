"""图纸生成单测：SVG 内容随 Spec 参数变化，且与 RTL/tb 同源。"""
import unittest

from hdc import diagram
from hdc.spec import from_dict


class TestDiagram(unittest.TestCase):
    def setUp(self):
        self.s = from_dict({
            "behavior": {"led_count": 4, "interval_ms": 500},
            "clock": {"freq_mhz": 50, "reset": "async_active_low"},
        })

    def test_block_diagram_has_module_and_ports(self):
        svg = diagram.generate_block_diagram(self.s)
        self.assertIn("led_chaser", svg)
        self.assertIn("rst_n", svg)          # async_active_low -> rst_n
        self.assertIn("en", svg)             # enable_port 默认开
        self.assertIn("led[3:0]", svg)       # 4 LED
        self.assertIn("DIVIDER=250000", svg)  # 50MHz*1000*500ms

    def test_state_diagram_has_n_states(self):
        svg = diagram.generate_state_diagram(self.s)
        for i in range(4):
            self.assertIn(f"S{i}</text>", svg)
        # left_to_right 复位态为 1000
        self.assertIn("4&#39;b1000", svg)
        self.assertIn("wrap 回到 S0", svg)

    def test_state_diagram_no_wrap_holds(self):
        s = from_dict({"behavior": {"led_count": 3, "wrap": False}})
        svg = diagram.generate_state_diagram(s)
        self.assertIn("hold（停在末端）", svg)
        self.assertNotIn("wrap 回到 S0", svg)

    def test_right_to_left_reset_pattern(self):
        s = from_dict({"behavior": {"led_count": 3, "direction": "right_to_left"}})
        svg = diagram.generate_state_diagram(s)
        self.assertIn("3&#39;b001", svg)  # 复位态为 001


if __name__ == "__main__":
    unittest.main()

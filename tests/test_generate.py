"""RTL / testbench 生成内容的结构化单测。"""
import unittest

from hdc import generate
from hdc.spec import from_dict


class TestRTL(unittest.TestCase):
    def setUp(self):
        self.s = from_dict({"behavior": {"led_count": 4, "direction": "left_to_right", "interval_ms": 1}})
        self.rtl = generate.generate_rtl(self.s)

    def test_module_name_and_params(self):
        self.assertIn("module led_chaser", self.rtl)
        self.assertIn("parameter LED_COUNT = 4", self.rtl)
        self.assertIn("parameter DIVIDER   = 50000", self.rtl)

    def test_ports(self):
        for p in ["clk", "rst_n", "en", "led"]:
            self.assertIn(p, self.rtl)

    def test_no_initial_block_in_synthesizable(self):
        self.assertNotIn("initial", self.rtl)

    def test_nonblocking_assignment(self):
        self.assertIn("<=", self.rtl)

    def test_shift_direction(self):
        self.assertIn("led >> 1", self.rtl)

    def test_wrap_vs_no_wrap(self):
        rtl_nowrap = generate.generate_rtl(from_dict({"behavior": {"wrap": False, "interval_ms": 1}}))
        self.assertIn("led != END_LED", rtl_nowrap)
        self.assertIn("led == END_LED", self.rtl)


class TestTB(unittest.TestCase):
    def setUp(self):
        self.s = from_dict({"behavior": {"led_count": 4, "direction": "left_to_right", "interval_ms": 1}})
        self.tb = generate.generate_tb(self.s)

    def test_assertion_names(self):
        for name in ["reset_initial_state", "hold_when_disabled", "interval",
                     "direction", "wrap_return", "no_unknown"]:
            self.assertIn(name, self.tb)

    def test_summary_marker(self):
        self.assertIn("SIM_RESULT: PASS", self.tb)
        self.assertIn("SIM_RESULT: FAIL", self.tb)

    def test_expected_sequence(self):
        self.assertIn("expect_seq[0] = 4'b0100;", self.tb)
        self.assertIn("expect_seq[3] = 4'b1000;", self.tb)

    def test_no_enable_port(self):
        tb = generate.generate_tb(from_dict({"behavior": {"enable_port": False, "interval_ms": 1}}))
        self.assertNotIn("reg en;", tb)
        self.assertNotIn('check("hold_when_disabled"', tb)


if __name__ == "__main__":
    unittest.main()

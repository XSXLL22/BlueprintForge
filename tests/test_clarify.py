"""需求澄清层单测：关键词提取、默认值兜底、歧义/不支持的提示。"""
import unittest

from hdc.clarify import clarify


class TestClarify(unittest.TestCase):
    def test_full_explicit_requirement(self):
        c = clarify("5 个灯，10 毫秒换一次，从左往右循环，50MHz，高电平复位，不带使能")
        s = c.to_spec()
        self.assertEqual(s.led_count, 5)
        self.assertEqual(s.interval_ms, 10)
        self.assertEqual(s.direction, "left_to_right")
        self.assertTrue(s.wrap)
        self.assertEqual(s.freq_mhz, 50)
        self.assertEqual(s.reset, "async_active_high")
        self.assertFalse(s.enable_port)
        # 全部字段显式给出 -> 无假设
        self.assertEqual(c.assumptions, [])

    def test_default_fallback(self):
        c = clarify("做个流水灯")
        s = c.to_spec()
        self.assertEqual(s.led_count, 4)       # 默认
        self.assertEqual(s.interval_ms, 500)   # 默认
        # 未指明的字段应全部进入假设清单
        self.assertTrue(any("led_count" in a for a in c.assumptions))
        self.assertTrue(any("interval_ms" in a for a in c.assumptions))

    def test_seconds_and_qualitative(self):
        self.assertEqual(clarify("1 秒换一次").to_spec().interval_ms, 1000)
        self.assertEqual(clarify("慢一点").to_spec().interval_ms, 1000)
        self.assertEqual(clarify("快一点").to_spec().interval_ms, 20)

    def test_right_to_left_and_stop(self):
        s = clarify("从右往左，到头就停").to_spec()
        self.assertEqual(s.direction, "right_to_left")
        self.assertFalse(s.wrap)

    def test_bounce_degrades_to_wrap(self):
        c = clarify("来回流动")
        s = c.to_spec()
        self.assertTrue(s.wrap)
        self.assertTrue(any("往复" in w or "乒乓" in w for w in c.warnings))

    def test_demo_requirement(self):
        c = clarify("帮我做一个流水灯，5 个灯，10 毫秒换一次，从左往右循环")
        s = c.to_spec()
        self.assertEqual(s.led_count, 5)
        self.assertEqual(s.interval_ms, 10)
        self.assertEqual(s.divider, 500000)  # 50MHz * 1000 * 10ms


if __name__ == "__main__":
    unittest.main()

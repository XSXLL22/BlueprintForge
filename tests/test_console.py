"""CLI 输出编码的单测。

起因是一条真实的崩溃：Windows 上把输出重定向到管道或文件时，Python 用的是本地
代码页（简中机器上是 GBK），而电路板摘要里有 `mm²`。GBK 编不出 `²`，于是
`python -m hdc counter.v --pcb > log.txt` 在**全链路已经跑完之后**、打印摘要那一
行抛 `UnicodeEncodeError` —— 板子做好了，用户只看到一段 traceback。

判据落在 `hdc.console.use_utf8()` 这个接口上：给它一个流，之后往流里写摘要里出
现的字符不能再抛异常。
"""
import io
import unittest

from hdc.console import use_utf8

#: 电路板摘要里真实出现过的字符，`²` 就是那条崩溃的元凶。
SUMMARY_SAMPLE = "  板图    : counter.kicad_pcb（铺铜 3942mm²，1 块）"


class TestUseUtf8(unittest.TestCase):
    def test_a_legacy_codepage_stream_can_afterwards_take_the_summary(self):
        """这一条就是那次崩溃的复现：改之前 write 会抛 UnicodeEncodeError。"""
        stream = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
        use_utf8(stream)
        stream.write(SUMMARY_SAMPLE)          # 崩溃点
        stream.flush()
        self.assertEqual(stream.encoding.lower().replace("_", "-"), "utf-8")

    def test_the_bytes_actually_written_are_utf8(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="gbk")
        use_utf8(stream)
        stream.write("铺铜 mm²")
        stream.flush()
        self.assertEqual(raw.getvalue().decode("utf-8"), "铺铜 mm²")

    def test_an_already_utf8_stream_is_left_usable(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        use_utf8(stream)
        stream.write(SUMMARY_SAMPLE)
        stream.flush()

    def test_a_stream_without_reconfigure_is_tolerated_not_crashed_on(self):
        """`StringIO` 没有 reconfigure —— 单测里到处在用它捕获输出。"""
        buf = io.StringIO()
        use_utf8(buf)
        buf.write(SUMMARY_SAMPLE)
        self.assertIn("mm²", buf.getvalue())

    def test_none_is_tolerated(self):
        """pythonw / 某些嵌入环境下 sys.stdout 就是 None。"""
        use_utf8(None)

    def test_it_reports_whether_it_changed_anything(self):
        gbk = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
        self.assertTrue(use_utf8(gbk))
        self.assertFalse(use_utf8(io.StringIO()))


if __name__ == "__main__":
    unittest.main()

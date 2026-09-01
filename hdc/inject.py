"""向已渲染的 RTL 故意注入错误，用于证明 testbench 能检出缺陷。

约定：DUT 用注入错误的 RTL，testbench 用正确 Spec 生成。正确 testbench 应当
对每一个注入的错误报 SIM_RESULT: FAIL。
"""
from __future__ import annotations

import re

BUG_TYPES = ("wrong_direction", "wrong_interval", "wrong_reset", "ignore_enable")


def apply(rtl: str, bug: str) -> str:
    """返回注入指定错误后的 RTL 文本。"""
    if bug == "wrong_direction":
        if "led >> 1" in rtl:
            return rtl.replace("led >> 1", "led << 1", 1)
        if "led << 1" in rtl:
            return rtl.replace("led << 1", "led >> 1", 1)
        raise ValueError("未找到 shift 操作符，无法注入 wrong_direction")

    if bug == "wrong_interval":
        # 改内部移位阈值（而非参数默认值，后者会被 tb 的 #(.DIVIDER) 覆盖）
        rtl, n = re.subn(r"tick == DIVIDER - 1", "tick == (DIVIDER / 2) - 1", rtl, count=1)
        if n == 0:
            raise ValueError("未找到 tick 比较，无法注入 wrong_interval")
        return rtl

    if bug == "wrong_reset":
        m_reset = re.search(r"localparam \[LED_COUNT-1:0\] RESET_LED = (\d+'b[01]+);", rtl)
        m_end = re.search(r"localparam \[LED_COUNT-1:0\] END_LED   = (\d+'b[01]+);", rtl)
        if not m_reset or not m_end:
            raise ValueError("未找到 RESET_LED/END_LED，无法注入 wrong_reset")
        return re.sub(
            r"(localparam \[LED_COUNT-1:0\] RESET_LED = )\d+'b[01]+",
            r"\g<1>" + m_end.group(1),
            rtl,
            count=1,
        )

    if bug == "ignore_enable":
        rtl, n = re.subn(r"else if \((?:en|!en)\) begin", "else if (1'b1) begin", rtl, count=1)
        if n == 0:
            raise ValueError("未找到使能条件，无法注入 ignore_enable")
        return rtl

    raise ValueError(f"未知错误类型: {bug}（可选 {BUG_TYPES}）")

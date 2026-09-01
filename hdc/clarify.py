"""需求澄清层（第 1 层）：自然语言一句话 -> Spec 覆盖字段 + 假设清单。

MVP 采用**确定性关键词提取 + 默认值兜底**：能识别的字段用提取值，识别不到的
按默认值并记录「假设」，等价于有界澄清（≤3 轮）在没有 LLM 时的退化实现。

生产环境把 :func:`clarify` 内部的规则替换为 LLM 结构化抽取即可，返回接口
（:class:`Clarification` -> :meth:`to_spec`）保持不变，下游完全无感。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from hdc.spec import Spec, from_dict

_NUM = r"(\d+(?:\.\d+)?)"


@dataclass
class Clarification:
    overrides: dict                    # 用户显式指定的字段（未合并默认值）
    recognized: list[str] = field(default_factory=list)   # 识别到的字段说明
    assumptions: list[str] = field(default_factory=list)  # 未指明、采用默认的字段说明
    warnings: list[str] = field(default_factory=list)     # 不支持/歧义的提示

    def to_spec(self) -> Spec:
        """合并默认值后解析为 Spec（校验由 hdc.spec 统一完成）。"""
        return from_dict(self.overrides)


def _set(node: dict, path: tuple[str, ...], value) -> None:
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


# 需要向用户报告「假设默认值」的字段：路径 -> 默认值展示
_TRACKED = [
    (("clock", "freq_mhz"), "clock.freq_mhz = 50"),
    (("clock", "reset"), "clock.reset = async_active_low"),
    (("behavior", "led_count"), "behavior.led_count = 4"),
    (("behavior", "direction"), "behavior.direction = left_to_right"),
    (("behavior", "interval_ms"), "behavior.interval_ms = 500"),
    (("behavior", "wrap"), "behavior.wrap = true"),
    (("behavior", "enable_port"), "behavior.enable_port = true"),
]


def clarify(requirement: str) -> Clarification:
    text = requirement
    overrides: dict = {}
    rec: list[str] = []
    warnings: list[str] = []
    keys: set[tuple[str, ...]] = set()

    def got(path: tuple[str, ...], label: str) -> None:
        keys.add(path)
        rec.append(label)

    # ---- LED 数量 -----------------------------------------------------------
    m = re.search(rf"{_NUM}\s*(?:个|路|颗|只|位|bit)?\s*(?:led|灯|指示灯)", text, re.I)
    if m:
        n = int(float(m.group(1)))
        if n >= 2:
            _set(overrides, ("behavior", "led_count"), n)
            got(("behavior", "led_count"), f"led_count = {n}")
        else:
            warnings.append(f"led_count={n} 过小，流水灯至少需要 2 个 LED，已忽略")

    # ---- 切换间隔（先于频率匹配，避免 "20ms" 里的 m 被误当频率）------------
    m = re.search(rf"{_NUM}\s*(?:ms|毫秒)", text, re.I)
    if m:
        _set(overrides, ("behavior", "interval_ms"), float(m.group(1)))
        got(("behavior", "interval_ms"), f"interval_ms = {float(m.group(1)):g}")
    else:
        m = re.search(rf"{_NUM}\s*(?:s|秒)", text)
        if m:
            _set(overrides, ("behavior", "interval_ms"), float(m.group(1)) * 1000.0)
            got(("behavior", "interval_ms"), f"interval_ms = {float(m.group(1)) * 1000:g}（{m.group(1)} 秒）")
        elif re.search(r"慢一点|慢点|慢一些|很慢|慢速", text):
            _set(overrides, ("behavior", "interval_ms"), 1000)
            got(("behavior", "interval_ms"), "interval_ms = 1000（“慢” → 1s）")
        elif re.search(r"快一点|快点|快一些|很快|快速", text):
            _set(overrides, ("behavior", "interval_ms"), 20)
            got(("behavior", "interval_ms"), "interval_ms = 20（“快” → 20ms）")

    # ---- 时钟频率 -----------------------------------------------------------
    m = re.search(rf"{_NUM}\s*(?:mhz|兆赫|兆)", text, re.I)
    if m:
        _set(overrides, ("clock", "freq_mhz"), float(m.group(1)))
        got(("clock", "freq_mhz"), f"freq_mhz = {float(m.group(1)):g}")

    # ---- 方向（物理可视方向，与 led 位移语义一致）--------------------------
    if re.search(r"从右到左|从右往左|向左|←", text):
        _set(overrides, ("behavior", "direction"), "right_to_left")
        got(("behavior", "direction"), "direction = right_to_left")
    elif re.search(r"从左到右|从左往右|向右|→", text):
        _set(overrides, ("behavior", "direction"), "left_to_right")
        got(("behavior", "direction"), "direction = left_to_right")

    # ---- 循环 / 停止（MVP 无往复模式）---------------------------------------
    if re.search(r"来回|往复|往返|乒乓", text):
        _set(overrides, ("behavior", "wrap"), True)
        got(("behavior", "wrap"), "wrap = true（往复模式 MVP 不支持，退化为循环）")
        warnings.append("「来回/往复」为乒乓模式，MVP 仅支持单向循环/停止，已退化为循环")
    elif re.search(r"到头(?:就)?停|不循环|只跑一遍|单向[^循环]|停止", text):
        _set(overrides, ("behavior", "wrap"), False)
        got(("behavior", "wrap"), "wrap = false")
    elif re.search(r"循环|循环滚动|一直(?:流动|转)", text):
        _set(overrides, ("behavior", "wrap"), True)
        got(("behavior", "wrap"), "wrap = true")

    # ---- 使能端口 -----------------------------------------------------------
    if re.search(r"(?:不|无|没|去除|去掉)\s*(?:带|要|需要)?\s*使能|禁用使能", text):
        _set(overrides, ("behavior", "enable_port"), False)
        got(("behavior", "enable_port"), "enable_port = false")
    elif re.search(r"使能|可暂停|enable", text, re.I):
        _set(overrides, ("behavior", "enable_port"), True)
        got(("behavior", "enable_port"), "enable_port = true")

    # ---- 复位类型 -----------------------------------------------------------
    if re.search(r"高(?:电平)?复位|高有效复位|复位高", text):
        _set(overrides, ("clock", "reset"), "async_active_high")
        got(("clock", "reset"), "reset = async_active_high")
    elif re.search(r"低(?:电平)?复位|低有效复位|复位低", text):
        _set(overrides, ("clock", "reset"), "async_active_low")
        got(("clock", "reset"), "reset = async_active_low")

    # ---- 假设清单（未识别的字段按默认值兜底）--------------------------------
    assumptions = [label for path, label in _TRACKED if path not in keys]

    return Clarification(
        overrides=overrides, recognized=rec,
        assumptions=assumptions, warnings=warnings,
    )

"""Spec 加载、校验与派生参数计算。

Spec JSON 是生成设计的唯一事实来源。下游所有层（RTL/tb 生成、仿真、综合）
只读取解析后的 :class:`Spec`，不直接触碰用户字符串。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---- 允许值 -----------------------------------------------------------------

RESET_TYPES = ("async_active_low", "async_active_high")
DIRECTIONS = ("left_to_right", "right_to_left")
POLARITIES = ("active_high", "active_low")

DEFAULTS: dict[str, Any] = {
    "project": "led_chaser",
    "type": "sequential",
    "clock": {"freq_mhz": 50, "reset": "async_active_low"},
    "behavior": {
        "led_count": 4,
        "direction": "left_to_right",
        "interval_ms": 500,
        "wrap": True,
        "enable_port": True,
        "enable_polarity": "active_high",
    },
    "target": "fpga",
    "constraints": {"toolchain": "iverilog+yosys", "max_fix_rounds": 3},
}

# divider 上限：确保 32-bit 计数器与参数不溢出
_MAX_DIVIDER = (1 << 31) - 1


class SpecError(ValueError):
    """Spec JSON 缺失或不一致时抛出。"""


@dataclass
class Spec:
    project: str
    freq_mhz: float
    reset: str
    led_count: int
    direction: str
    interval_ms: float
    wrap: bool
    enable_port: bool
    enable_polarity: str
    target: str
    max_fix_rounds: int
    raw: dict = field(default_factory=dict)

    # ---- 派生参数 -----------------------------------------------------------

    @property
    def divider(self) -> int:
        """每个间隔的时钟周期数 = freq_mhz * 1000 * interval_ms。"""
        return int(round(self.freq_mhz * 1000.0 * self.interval_ms))

    @property
    def reset_port(self) -> str:
        return "rst_n" if self.reset == "async_active_low" else "rst"

    @property
    def reset_active(self) -> str:
        return "1'b0" if self.reset == "async_active_low" else "1'b1"

    @property
    def reset_inactive(self) -> str:
        return "1'b1" if self.reset == "async_active_low" else "1'b0"

    @property
    def reset_sensitivity(self) -> str:
        return " or negedge rst_n" if self.reset == "async_active_low" else " or posedge rst"

    @property
    def reset_active_cond(self) -> str:
        return "!rst_n" if self.reset == "async_active_low" else "rst"

    @property
    def enable_active(self) -> str:
        return "1'b1" if self.enable_polarity == "active_high" else "1'b0"

    @property
    def enable_inactive(self) -> str:
        return "1'b0" if self.enable_polarity == "active_high" else "1'b1"

    @property
    def enable_cond(self) -> str:
        if not self.enable_port:
            return "1'b1"
        return "en" if self.enable_polarity == "active_high" else "!en"

    @property
    def reset_pattern(self) -> str:
        """复位后 LED 位型（MSB..LSB 二进制串）。"""
        n = self.led_count
        return "1" + "0" * (n - 1) if self.direction == "left_to_right" else "0" * (n - 1) + "1"

    @property
    def end_pattern(self) -> str:
        """流水到达的最远端位型。"""
        n = self.led_count
        return "0" * (n - 1) + "1" if self.direction == "left_to_right" else "1" + "0" * (n - 1)

    @property
    def tick_width(self) -> int:
        """计数器位宽，足以表示 divider-1。"""
        return max(1, (self.divider - 1).bit_length())

    @property
    def tick_msb(self) -> int:
        return self.tick_width - 1

    @property
    def half_ns(self) -> str:
        """时钟半周期（ns），6 位小数，供 testbench 生成时钟。"""
        return f"{500.0 / self.freq_mhz:.6f}"

    # ---- 期望序列 -----------------------------------------------------------

    def expected_sequence(self) -> list[int]:
        """每次移位后的期望 LED 位型（int），按顺序返回。

        wrap=True   -> led_count 项，最后一项回到 RESET。
        wrap=False  -> led_count-1 项，最后一项停在 END。
        """
        n = self.led_count
        reset = (1 << (n - 1)) if self.direction == "left_to_right" else 1
        end = 1 if self.direction == "left_to_right" else (1 << (n - 1))
        steps = n if self.wrap else n - 1
        seq: list[int] = []
        cur = reset
        for _ in range(steps):
            if cur == end:
                cur = reset
            elif self.direction == "left_to_right":
                cur >>= 1
            else:
                cur <<= 1
            seq.append(cur)
        return seq

    def literal(self, value: int) -> str:
        """把 int 位型渲染为 Verilog 位宽字面量，如 4'b0100。"""
        return f"{self.led_count}'b{value:0{self.led_count}b}"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _validate(data: dict) -> dict:
    data = _deep_merge(DEFAULTS, data)

    project = str(data["project"])
    if not project.isidentifier():
        raise SpecError(f"project '{project}' 不是合法 Verilog 标识符")

    clock = data["clock"]
    freq = float(clock["freq_mhz"])
    if freq <= 0:
        raise SpecError("clock.freq_mhz 必须 > 0")

    reset = clock["reset"]
    if reset not in RESET_TYPES:
        raise SpecError(f"clock.reset 不支持 '{reset}'（仅支持 {RESET_TYPES}）")

    b = data["behavior"]
    led_count = int(b["led_count"])
    if led_count < 2:
        raise SpecError(
            f"behavior.led_count 必须 >= 2（当前 {led_count}）：流水灯至少需要 2 个 LED"
        )

    direction = b["direction"]
    if direction not in DIRECTIONS:
        raise SpecError(f"behavior.direction 不支持 '{direction}'（仅支持 {DIRECTIONS}）")

    interval_ms = float(b["interval_ms"])
    if interval_ms <= 0:
        raise SpecError("behavior.interval_ms 必须 > 0")

    divider = int(round(freq * 1000.0 * interval_ms))
    if not (2 <= divider <= _MAX_DIVIDER):
        raise SpecError(
            f"由 freq={freq:g}MHz / interval={interval_ms:g}ms 得到的 divider={divider} "
            f"超出支持范围 [2, {_MAX_DIVIDER}]"
        )

    wrap = bool(b["wrap"])
    enable_port = bool(b.get("enable_port", True))
    enable_polarity = b.get("enable_polarity", "active_high")
    if enable_polarity not in POLARITIES:
        raise SpecError(f"behavior.enable_polarity 不支持 '{enable_polarity}'")

    return {
        "project": project,
        "freq_mhz": freq,
        "reset": reset,
        "led_count": led_count,
        "direction": direction,
        "interval_ms": interval_ms,
        "wrap": wrap,
        "enable_port": enable_port,
        "enable_polarity": enable_polarity,
        "target": str(data.get("target", "fpga")),
        "max_fix_rounds": int(data.get("constraints", {}).get("max_fix_rounds", 3)),
        "raw": data,
    }


def from_dict(raw: dict) -> Spec:
    return Spec(**_validate(raw))


def load(path: str | Path) -> Spec:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SpecError(f"Spec 文件不存在: {p}") from None
    except json.JSONDecodeError as e:
        raise SpecError(f"Spec JSON 解析失败: {e}") from None
    return from_dict(raw)

"""从 Spec 渲染可综合 RTL 与自检 testbench。

模板为主：RTL/tb 的静态骨架在 templates/ 下，这里只把 Spec 参数与少量派生
逻辑填入占位符。LLM（未来）仅负责填参数，不自由编写 HDL。
"""
from __future__ import annotations

import re
from pathlib import Path

from hdc.spec import Spec

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(template: str, values: dict) -> str:
    """替换 `{{ key }}` 占位符。缺键时抛出，避免静默产出残缺代码。"""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"模板占位符 {{{{{key}}}}} 缺少对应值")
        return str(values[key])

    return _PLACEHOLDER.sub(sub, template)


def load_template(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


# ---- RTL --------------------------------------------------------------------

def _shift_logic(spec: Spec) -> str:
    """生成 shift 逻辑块（已带缩进，供模板原样插入）。"""
    op = "led >> 1" if spec.direction == "left_to_right" else "led << 1"
    ind, in2 = " " * 16, " " * 20
    if spec.wrap:
        return (
            f"{ind}if (led == END_LED)\n"
            f"{in2}led <= RESET_LED;\n"
            f"{ind}else\n"
            f"{in2}led <= {op};\n"
        )
    return (
        f"{ind}if (led != END_LED)\n"
        f"{in2}led <= {op};\n"
    )


def rtl_values(spec: Spec) -> dict:
    return {
        "project": spec.project,
        "led_count": spec.led_count,
        "divider": spec.divider,
        "freq_mhz": f"{spec.freq_mhz:g}",
        "interval_ms": f"{spec.interval_ms:g}",
        "direction": spec.direction,
        "wrap": "true" if spec.wrap else "false",
        "reset": spec.reset,
        "reset_port": spec.reset_port,
        "reset_sensitivity": spec.reset_sensitivity,
        "reset_active_cond": spec.reset_active_cond,
        "reset_pattern": spec.reset_pattern,
        "end_pattern": spec.end_pattern,
        "enable_port_decl": "    input  wire              en,\n" if spec.enable_port else "",
        "enable_cond": spec.enable_cond,
        "enable_port": "yes" if spec.enable_port else "no",
        "enable_polarity": spec.enable_polarity,
        "tick_msb": spec.tick_msb,
        "shift_logic": _shift_logic(spec),
    }


def generate_rtl(spec: Spec) -> str:
    return render(load_template("led_chaser.v.tpl"), rtl_values(spec))


# ---- testbench --------------------------------------------------------------

def _stimulus_prelude(spec: Spec) -> str:
    """复位/使能/保持 的激励前导（含 enable 与否的两分支）。"""
    rp = spec.reset_port
    if spec.enable_port:
        return (
            f"        // 1. reset -> initial state\n"
            f"        {rp} = {spec.reset_active};\n"
            f"        en = {spec.enable_inactive};\n"
            f"        repeat (10) @(posedge clk);\n"
            f"        #1;\n"
            f"        check(\"reset_initial_state\", led === RESET_LED);\n\n"
            f"        // 2. release reset; must hold while disabled\n"
            f"        {rp} = {spec.reset_inactive};\n"
            f"        repeat (DIVIDER + 10) @(posedge clk);\n"
            f"        #1;\n"
            f"        check(\"hold_when_disabled\", led === RESET_LED);\n\n"
            f"        // 3. enable -> sweep through the expected sequence\n"
            f"        en = {spec.enable_active};\n"
            f"        prev_led = led;\n"
        )
    return (
        f"        // 1. reset -> initial state\n"
        f"        {rp} = {spec.reset_active};\n"
        f"        repeat (10) @(posedge clk);\n"
        f"        #1;\n"
        f"        check(\"reset_initial_state\", led === RESET_LED);\n\n"
        f"        // 2. release reset (always enabled) -> sweep\n"
        f"        {rp} = {spec.reset_inactive};\n"
        f"        prev_led = led;\n"
    )


def _transition_block(spec: Spec, n_steps: int) -> str:
    return (
        f"        for (step = 0; step < {n_steps}; step = step + 1) begin\n"
        f"            $display(\"-- step %0d: expecting led = %0b --\", step, expect_seq[step]);\n"
        f"            cycles = 0;\n"
        f"            while (led === prev_led && cycles < (DIVIDER * 2)) begin\n"
        f"                @(posedge clk);\n"
        f"                #1;\n"
        f"                cycles = cycles + 1;\n"
        f"            end\n"
        f"            check(\"interval\", cycles == DIVIDER);\n"
        f"            check(\"direction\", led === expect_seq[step]);\n"
        f"            prev_led = led;\n"
        f"        end\n"
    )


def _end_behavior_block(spec: Spec) -> str:
    if spec.wrap:
        return "        check(\"wrap_return\", led === RESET_LED);\n"
    return (
        "        repeat (DIVIDER + 10) @(posedge clk);\n"
        "        #1;\n"
        "        check(\"no_wrap_hold\", led === END_LED);\n"
    )


def tb_values(spec: Spec) -> dict:
    seq = spec.expected_sequence()
    n_steps = len(seq)
    expect_init = "\n".join(
        f"        expect_seq[{i}] = {spec.literal(v)};" for i, v in enumerate(seq)
    )
    return {
        "project": spec.project,
        "led_count": spec.led_count,
        "divider": spec.divider,
        "reset_pattern": spec.reset_pattern,
        "end_pattern": spec.end_pattern,
        "reset_port": spec.reset_port,
        "half_ns": spec.half_ns,
        "en_port_decl": "    reg en;\n" if spec.enable_port else "",
        "en_port_connect": "        .en (en),\n" if spec.enable_port else "",
        "n_steps_minus_1": n_steps - 1,
        "expect_seq_init": expect_init + ("\n" if n_steps else ""),
        "stimulus_prelude": _stimulus_prelude(spec),
        "transition_block": _transition_block(spec, n_steps),
        "end_behavior_block": _end_behavior_block(spec),
    }


def generate_tb(spec: Spec) -> str:
    return render(load_template("tb_led_chaser.v.tpl"), tb_values(spec))

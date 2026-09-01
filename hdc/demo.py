"""端到端演示：自然语言需求 -> 澄清 -> Spec -> 仿真/综合 -> 打包。

用一段中文需求跑完整闭环，把澄清过程、Spec 解析、验证结果与产物清单打印出来，
作为「AI 辅助数字硬件设计闭环」的可复现演示。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hdc.clarify import clarify
from hdc.pipeline import build

DEFAULT_REQUIREMENT = "帮我做一个流水灯，5 个灯，10 毫秒换一次，从左往右循环"


def run_demo(requirement: str | None = None, out_root: Path = Path("output")) -> int:
    if requirement is None:
        requirement = DEFAULT_REQUIREMENT
    hr = "=" * 72
    print(hr)
    print("  hdc 端到端演示：自然语言需求 → 澄清 → Spec → 仿真/综合 → 打包")
    print(hr)
    print(f"\n[需求] {requirement}")

    c = clarify(requirement)

    print("\n[澄清] 关键词提取 + 默认值兜底（有界澄清的确定性退化实现）")
    if c.recognized:
        print("  识别:")
        for r in c.recognized:
            print(f"    ✔ {r}")
    if c.assumptions:
        print("  假设（需求未指明，采用默认）:")
        for a in c.assumptions:
            print(f"    ? {a}")
    if c.warnings:
        print("  提示:")
        for w in c.warnings:
            print(f"    ! {w}")

    spec = c.to_spec()
    print(f"\n[Spec] 解析结果（divider={spec.divider} 周期 / {spec.tick_width} bit 计数器 / "
          f"TICK_MSB={spec.tick_msb}）")
    print(json.dumps(spec.raw, ensure_ascii=False, indent=2))

    # Spec 是唯一事实来源：先落盘，再走统一 build（不复用内部构造，保持不变式）
    with tempfile.TemporaryDirectory() as d:
        spec_path = Path(d) / "demo_spec.json"
        spec_path.write_text(json.dumps(spec.raw, ensure_ascii=False, indent=2), encoding="utf-8")
        result = build(spec_path, out_root)

    print("\n[验证]")
    _print_sim(result)
    _print_synth(result)

    print(f"\n[产物] {result.out_dir}")
    for rel in ["rtl/led_chaser.v", "tb/tb_led_chaser.v",
                "diagrams/block_diagram.svg", "diagrams/state_diagram.svg",
                "docs/spec.json", "docs/report.md"]:
        p = result.out_dir / rel
        print(f"  {'✓' if p.exists() else '✗'} {rel}")

    for s in result.skipped:
        print(f"  [跳过] {s}")

    print(hr)
    print(f"  结论：{'OK' if result.ok else 'NG'}")
    print(hr)
    return 0 if result.ok else 1


def _print_sim(result) -> None:
    sim = result.sim
    if sim is None:
        print("  仿真    : 跳过（工具链未安装）")
    elif not sim.compile_ok:
        print("  仿真    : 编译失败（compile_error）")
    elif sim.passed:
        print("  仿真    : 通过（复位/使能/方向/间隔/wrap/无 X-Z 全部断言 PASS）")
    else:
        print(f"  仿真    : 失败（assertion_failure，{sim.checks_failed} 个断言未通过）")


def _print_synth(result) -> None:
    synth = result.synth
    if synth is None:
        print("  综合    : 跳过（工具链未安装）")
    elif synth.ok:
        latch = "，含锁存器推断警告" if synth.latches_inferred else ""
        print(f"  综合    : 通过（可综合{latch}）")
    else:
        print("  综合    : 失败（synthesis_error）")

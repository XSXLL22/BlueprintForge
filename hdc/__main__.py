"""命令行入口：python -m hdc <spec.json> [选项]。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hdc.inject import BUG_TYPES
from hdc.pipeline import build
from hdc.toolchain import detect


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="hdc",
        description="AI 辅助数字硬件设计闭环（MVP）：从 Spec 生成流水灯并仿真/综合验证",
    )
    p.add_argument("spec", help="Spec JSON 路径")
    p.add_argument("--out", default="output", help="输出根目录（默认 output）")
    p.add_argument("--no-sim", action="store_true", help="跳过仿真")
    p.add_argument("--no-synth", action="store_true", help="跳过综合")
    p.add_argument("--dump", action="store_true", help="仿真时导出 VCD 波形")
    p.add_argument(
        "--inject", choices=BUG_TYPES,
        help="向 RTL 注入错误以验证 testbench 能检出（预期仿真 FAIL）",
    )
    args = p.parse_args(argv)

    result = build(
        Path(args.spec),
        Path(args.out),
        run_sim=not args.no_sim,
        run_synth=not args.no_synth,
        dump_vcd=args.dump,
        inject=args.inject,
    )

    _summarize(result)
    return 0 if result.ok else 1


def _summarize(r) -> None:
    print(f"\n=== {r.project} 构建结果 ===")
    print(f"  输出目录: {r.out_dir}")
    print(f"  RTL     : {r.rtl_path}")
    print(f"  TB      : {r.tb_path}")

    if r.injected_bug:
        print(f"  [注入]  : {r.injected_bug}（预期仿真 FAIL）")

    if r.sim is None:
        print("  仿真    : 跳过")
    elif not r.sim.compile_ok:
        print("  仿真    : 编译失败（compile_error）")
    elif r.sim.passed:
        print("  仿真    : 通过")
    else:
        print(f"  仿真    : 失败（assertion_failure，{r.sim.checks_failed} 个断言未通过）")

    if r.synth is None:
        print("  综合    : 跳过")
    elif r.synth.ok:
        latch = "（含锁存器推断警告）" if r.synth.latches_inferred else ""
        print(f"  综合    : 通过{latch}")
    else:
        print("  综合    : 失败（synthesis_error）")

    for s in r.skipped:
        print(f"  [跳过]  : {s}")

    print(f"  结论    : {'OK' if r.ok else 'NG'}\n")


if __name__ == "__main__":
    sys.exit(main())

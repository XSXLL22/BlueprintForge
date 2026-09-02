"""命令行入口：python -m hdc <spec.json> [选项]，或 python -m hdc --demo [需求]。

加 `--pcb` 就在验证通过之后继续做板子：RTL → 74HC 分立门 → 原理图 → 板图 → DRC
→ 嘉立创可上传的 Gerber/BOM/CPL。`--pcb` 也接受直接给一个 `.v`（只做板子）。
"""
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
    p.add_argument("spec", nargs="?", help="Spec JSON 路径；--demo/--design 时为一句自然语言需求")
    p.add_argument("--demo", action="store_true", help="运行端到端演示（需求 → 澄清 → Spec → 闭环）")
    p.add_argument("--design", action="store_true",
                   help="用接入的 LLM API 从自然语言需求自动设计（交付期，自动仿真+综合+修复闭环）")
    p.add_argument("--out", default="output", help="输出根目录（默认 output）")
    p.add_argument("--no-sim", action="store_true", help="跳过仿真")
    p.add_argument("--no-synth", action="store_true", help="跳过综合")
    p.add_argument("--dump", action="store_true", help="仿真时导出 VCD 波形")
    p.add_argument("--pcb", action="store_true",
                   help="验证通过后继续做成 74HC 电路板：原理图、板图、DRC、"
                        "嘉立创可上传的 Gerber/BOM/CPL（需要 KiCad）")
    p.add_argument(
        "--inject", choices=BUG_TYPES,
        help="向 RTL 注入错误以验证 testbench 能检出（预期仿真 FAIL）",
    )
    args = p.parse_args(argv)

    if args.demo:
        from hdc.demo import run_demo
        return run_demo(args.spec, Path(args.out))

    if args.design:
        if not args.spec:
            p.error('--design 需要一句自然语言需求，例如：python -m hdc --design "做个 4 位计数器"')
        return _run_design(
            args.spec, Path(args.out),
            run_synth=not args.no_synth, dump_vcd=args.dump, run_pcb=args.pcb,
        )

    if not args.spec:
        p.error("缺少 Spec JSON 路径（或使用 --demo 运行演示 / --design 用 LLM 设计）")

    if args.pcb and Path(args.spec).suffix.lower() in (".v", ".sv"):
        rtl = Path(args.spec)          # 直接从 RTL 起步：只做板子，不再仿真综合
        return _run_pcb(rtl, rtl.stem, Path(args.out) / rtl.stem / "pcb")

    result = build(
        Path(args.spec),
        Path(args.out),
        run_sim=not args.no_sim,
        run_synth=not args.no_synth,
        dump_vcd=args.dump,
        inject=args.inject,
    )

    _summarize(result)
    if not args.pcb:
        return 0 if result.ok else 1
    if not result.ok:
        print("  [跳过]  : 电路板 —— 逻辑没验证通过，先把 RTL 修对再做板子\n")
        return 1
    return _run_pcb(result.rtl_path, result.project, result.out_dir / "pcb")


def _run_design(requirement: str, out_dir: Path, *, run_synth: bool = True,
                dump_vcd: bool = False, run_pcb: bool = False) -> int:
    """--design：接入 LLM API 的自动设计闭环（交付期）。"""
    from hdc.diagram import write_design_diagrams
    from hdc.llm import LLMError, design_with_fix, provider_from_config

    try:
        provider = provider_from_config()
    except LLMError as e:
        print(f"[配置错误] {e}")
        print("请设置环境变量 HDC_PROVIDER / HDC_API_KEY / HDC_MODEL，或写入 ~/.hdc/config。")
        print("示例（Ollama 本地，无需 key）：HDC_PROVIDER=ollama python -m hdc --design \"做个计数器\"")
        return 2

    print(f"[LLM] 需求：{requirement}")
    try:
        out = design_with_fix(provider, requirement, out_dir, run_synth=run_synth, dump_vcd=dump_vcd)
    except LLMError as e:
        print(f"[LLM 调用失败] {e}")
        return 2
    diagrams = write_design_diagrams(out.design.design_json, out_dir / "diagrams")

    print(f"\n=== {out.design.project} 设计结果 ===")
    print(f"  输出目录: {out_dir}")
    if out.sim is not None:
        print(f"  仿真    : {'通过' if out.sim.passed else f'失败（{out.sim.error_class}）'}")
    if out.synth is not None:
        print(f"  综合    : {'通过' if out.synth.ok else f'失败（{out.synth.error_class}）'}")
    for s in out.skipped:
        print(f"  [跳过]  : {s}")
    if diagrams:
        print(f"  图纸    : " + ", ".join(str(d.relative_to(out_dir)) for d in diagrams))
    if not out.ok:
        for e in out.errors():
            print(f"  [错误]  : {e}")
    print(f"  结论    : {'OK' if out.ok else 'NG'}\n")
    if not run_pcb:
        return 0 if out.ok else 1
    if not out.ok:
        print("  [跳过]  : 电路板 —— 逻辑没验证通过，先把 RTL 修对再做板子\n")
        return 1
    rtl = out_dir / "rtl" / f"{out.design.project}.v"
    return _run_pcb(rtl, out.design.project, out_dir / "pcb")


def _run_pcb(rtl: Path, project: str, out_dir: Path) -> int:
    """--pcb：RTL → 74HC 电路板 → 嘉立创可上传文件。"""
    from hdc.pcb.kicad import KicadError
    from hdc.pcb.manufacture import FabError
    from hdc.pcb.pipeline import build_pcb

    print(f"\n[PCB] {rtl} → 74HC 分立门电路板")
    try:
        r = build_pcb(rtl, project, out_dir)
    except (FabError, KicadError) as e:
        print(f"[电路板构建失败] {e}")
        return 2

    print(f"\n=== {r.project} 电路板 ===")
    print(f"  输出目录: {r.out_dir}")
    print("  芯片清单: " + ("、".join(f"{name} ×{n}" for name, n in
                                  sorted(r.chips.items())) or "（空）"))
    print(f"  原理图  : {r.schematic_file.name}")
    if r.pcb_file is not None:
        print(f"  板图    : {r.pcb_file.name}"
              f"（铺铜 {r.zone_area:.0f}mm²，{r.zone_islands} 块）")
    if r.drc is not None:
        print(f"  DRC     : {'通过' if r.drc.ok else '有违规 → ' + r.drc.path.name}")
    if r.fabrication is not None:
        fab = r.fabrication
        print(f"  制造文件: gerber/（{len(fab.gerbers)} 个）+ {fab.drill.name}"
              f" + {fab.bom.name} + {fab.cpl.name}")
        print(f"  图纸    : {fab.schematic_pdf.name}、{fab.board_pdf.name}")
        print(f"  可上传  : {fab.archive.name}")
    for w in r.warnings:
        print(f"  [警告]  : {w}")
    for e in r.errors():
        print(f"  [错误]  : {e}")
    print(f"  结论    : {'OK' if r.ok else 'NG'}\n")
    return 0 if r.ok else 1


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

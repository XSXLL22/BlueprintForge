"""编排闭环：加载 Spec -> 生成 RTL/tb -> 仿真 -> 综合 -> 打包输出。"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from hdc import generate
from hdc.inject import apply as inject_apply
from hdc.spec import Spec, load
from hdc.toolchain import detect
from hdc.verify import SimResult, SynthResult, run_simulation, run_synthesis


@dataclass
class BuildResult:
    project: str
    out_dir: Path
    spec: Spec
    rtl_path: Path
    tb_path: Path
    sim: SimResult | None
    synth: SynthResult | None
    injected_bug: str | None
    skipped: list

    @property
    def ok(self) -> bool:
        ok = True
        if self.sim is not None:
            ok = ok and self.sim.passed
        if self.synth is not None:
            ok = ok and self.synth.ok
        return ok


def build(
    spec_path: Path,
    out_root: Path,
    *,
    run_sim: bool = True,
    run_synth: bool = True,
    dump_vcd: bool = False,
    inject: str | None = None,
) -> BuildResult:
    spec = load(spec_path)
    tc = detect()
    skipped: list[str] = []

    out_dir = out_root / spec.project
    rtl_dir = out_dir / "rtl"
    tb_dir = out_dir / "tb"
    sim_dir = out_dir / "sim"
    synth_dir = out_dir / "synth"
    docs_dir = out_dir / "docs"
    for d in (rtl_dir, tb_dir, sim_dir, synth_dir, docs_dir):
        d.mkdir(parents=True, exist_ok=True)

    rtl_text = generate.generate_rtl(spec)
    tb_text = generate.generate_tb(spec)

    # 错误注入只作用于 DUT；testbench 保持正确，用以检出缺陷
    if inject:
        rtl_text = inject_apply(rtl_text, inject)

    rtl_path = rtl_dir / f"{spec.project}.v"
    tb_path = tb_dir / f"tb_{spec.project}.v"
    rtl_path.write_text(rtl_text, encoding="utf-8")
    tb_path.write_text(tb_text, encoding="utf-8")

    # 仿真
    sim: SimResult | None = None
    if run_sim:
        if tc.can_simulate:
            sim = run_simulation(tc, rtl_path, tb_path, sim_dir, spec.project, dump_vcd=dump_vcd)
        else:
            skipped.append("sim (iverilog/vvp 未找到)")

    # 综合
    synth: SynthResult | None = None
    if run_synth:
        if tc.can_synthesize:
            synth = run_synthesis(tc, rtl_path, synth_dir, spec.project)
        else:
            skipped.append("synth (yosys 未找到)")

    # 打包：spec 副本 + 报告 + README
    (docs_dir / "spec.json").write_text(json.dumps(spec.raw, ensure_ascii=False, indent=2), encoding="utf-8")
    (docs_dir / "report.md").write_text(_report(spec, sim, synth, inject), encoding="utf-8")
    (out_dir / "README.md").write_text(_project_readme(spec), encoding="utf-8")

    return BuildResult(
        project=spec.project, out_dir=out_dir, spec=spec,
        rtl_path=rtl_path, tb_path=tb_path, sim=sim, synth=synth,
        injected_bug=inject, skipped=skipped,
    )


# ---- 报告 -------------------------------------------------------------------

def _report(spec: Spec, sim: SimResult | None, synth: SynthResult | None, inject: str | None) -> str:
    L = []
    L.append(f"# {spec.project} 设计报告\n")
    L.append("## 参数\n")
    L.append("| 字段 | 值 |")
    L.append("|------|----|")
    L.append(f"| project | {spec.project} |")
    L.append(f"| clock.freq_mhz | {spec.freq_mhz:g} |")
    L.append(f"| clock.reset | {spec.reset} |")
    L.append(f"| led_count | {spec.led_count} |")
    L.append(f"| direction | {spec.direction} |")
    L.append(f"| interval_ms | {spec.interval_ms:g} |")
    L.append(f"| divider (cycles) | {spec.divider} |")
    L.append(f"| wrap | {str(spec.wrap).lower()} |")
    L.append(f"| enable_port | {str(spec.enable_port).lower()} |")
    L.append(f"| enable_polarity | {spec.enable_polarity} |")
    if inject:
        L.append(f"| injected_bug | {inject} |")
    L.append("")

    L.append("## 仿真\n")
    if sim is None:
        L.append("跳过（工具链未安装）。\n")
    elif not sim.compile_ok:
        L.append("**编译失败**\n\n```\n" + sim.compile_output.strip() + "\n```\n")
    elif sim.passed:
        L.append("**通过**：所有断言（复位初始态 / 使能保持 / 方向 / 间隔 / wrap / 无 X-Z）均满足。\n")
    else:
        L.append(f"**失败**：{sim.checks_failed} 个断言未通过（错误分类: {sim.error_class}）。\n")
        L.append("```\n" + sim.sim_output.strip() + "\n```\n")

    L.append("## 综合\n")
    if synth is None:
        L.append("跳过（工具链未安装）。\n")
    elif synth.ok:
        latch = "，**注意：检测到锁存器推断**" if synth.latches_inferred else "，无锁存器推断"
        L.append(f"**通过**（可综合）{latch}。\n")
    else:
        L.append("**失败**（存在不可综合结构）。\n")
        for e in synth.error_lines:
            L.append(f"- `{e}`")
        L.append("")

    return "\n".join(L)


def _project_readme(spec: Spec) -> str:
    return (
        f"# {spec.project}\n\n"
        f"自动生成的设计包（由 hdc 工具链从 Spec 生成）。\n\n"
        f"- RTL: `rtl/{spec.project}.v`\n"
        f"- Testbench: `tb/tb_{spec.project}.v`\n"
        f"- 仿真日志: `sim/sim.log`\n"
        f"- 综合日志: `synth/synth.log`\n"
        f"- 报告: `docs/report.md`\n\n"
        f"参数：{spec.led_count} 个 LED，{spec.freq_mhz:g} MHz，{spec.interval_ms:g} ms，"
        f"{spec.direction}，wrap={spec.wrap}，enable_port={spec.enable_port}。\n"
    )

"""LLM 自由设计产物契约 + 验证编排。

LLM（开发期是 Claude，交付期是用户自己的 API）产出一份 :class:`Design`；本模块负责：

- 定义产物结构（唯一事实来源从「Spec」变为「LLM 产出」）
- 落盘 / 加载（``write_artifacts`` / ``load_artifacts``）
- 复用 :mod:`hdc.verify` 的仿真 / 综合做单轮验证（``verify_design``）

设计刻意**只约定最小硬契约**（RTL 顶层 = project、TB 顶层 = tb_project、TB 打印
``SIM_RESULT: PASS``、RTL 可综合），其余端口名 / 参数名 / 内部结构完全自由——这样 LLM
才能自由设计计数器 / PWM / 任意状态机，而不是被流水灯的专用锚点锁死。

「失败 → 错误分类 → 重写」的有界修复循环不在这里实现：阶段 A 由 Claude（skill）驱动，
阶段 B 由 ``hdc.llm`` 的 provider 驱动，两者都复用本模块的单轮 ``verify_design``。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from hdc.toolchain import Toolchain, detect
from hdc.verify import SimResult, SynthResult, run_simulation, run_synthesis


@dataclass
class Design:
    """一次 LLM 设计的完整产物。"""
    project: str
    requirement: str
    rtl: str                          # 可综合 Verilog 源码
    tb: str                           # 自检 testbench 源码
    design_json: dict                 # 结构化描述（interface 必需）
    state_machine_md: str = ""        # 状态机描述（含 Mermaid）
    concept_md: str = ""              # 逻辑电路设计构想

    @property
    def rtl_top(self) -> str:
        return self.project

    @property
    def tb_top(self) -> str:
        return f"tb_{self.project}"


@dataclass
class VerifyOutcome:
    design: Design
    sim: SimResult | None
    synth: SynthResult | None
    skipped: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        ok = True
        if self.sim is not None:
            ok = ok and self.sim.passed
        if self.synth is not None:
            ok = ok and self.synth.ok
        return ok

    def errors(self) -> list[str]:
        """汇总本轮的错误分类，供 LLM 定向修复。"""
        out: list[str] = []
        if self.sim is not None and not self.sim.passed:
            out.append(f"sim:{self.sim.error_class}")
            if self.sim.sim_output:
                out.append(self.sim.sim_output.strip())
        if self.synth is not None and not self.synth.ok:
            out.append(f"synth:{self.synth.error_class}")
            out.extend(f"  {e}" for e in self.synth.error_lines)
        return out


# ---- 产物落盘 / 加载 ---------------------------------------------------------

def write_artifacts(design: Design, out_dir: Path) -> dict[str, Path]:
    """把设计产物写到 out_dir，返回关键文件路径（rtl/tb）。"""
    out_dir = Path(out_dir)
    rtl_dir = out_dir / "rtl"
    tb_dir = out_dir / "tb"
    rtl_dir.mkdir(parents=True, exist_ok=True)
    tb_dir.mkdir(parents=True, exist_ok=True)

    rtl_path = rtl_dir / f"{design.project}.v"
    tb_path = tb_dir / f"tb_{design.project}.v"
    rtl_path.write_text(design.rtl, encoding="utf-8")
    tb_path.write_text(design.tb, encoding="utf-8")
    (out_dir / "design.json").write_text(
        json.dumps(design.design_json, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    if design.state_machine_md:
        (out_dir / "state_machine.md").write_text(design.state_machine_md, encoding="utf-8")
    if design.concept_md:
        (out_dir / "concept.md").write_text(design.concept_md, encoding="utf-8")
    return {"rtl": rtl_path, "tb": tb_path}


def load_artifacts(out_dir: Path) -> Design:
    """从 out_dir 读回一份设计产物（供往返测试 / 二次验证）。"""
    out_dir = Path(out_dir)
    project = (out_dir / "design.json").read_text(encoding="utf-8")
    dj = json.loads(project)
    project = dj["project"]
    rtl = (out_dir / "rtl" / f"{project}.v").read_text(encoding="utf-8")
    tb = (out_dir / "tb" / f"tb_{project}.v").read_text(encoding="utf-8")
    sm = (out_dir / "state_machine.md").read_text(encoding="utf-8") if (out_dir / "state_machine.md").exists() else ""
    concept = (out_dir / "concept.md").read_text(encoding="utf-8") if (out_dir / "concept.md").exists() else ""
    return Design(
        project=project, requirement=dj.get("requirement", ""), rtl=rtl, tb=tb,
        design_json=dj, state_machine_md=sm, concept_md=concept,
    )


# ---- 验证编排 ----------------------------------------------------------------

def verify_design(
    design: Design,
    out_dir: Path,
    *,
    run_sim: bool = True,
    run_synth: bool = True,
    dump_vcd: bool = False,
    tc: Toolchain | None = None,
) -> VerifyOutcome:
    """对一份 Design 做单轮验证（仿真 + 综合），复用 hdc.verify。

    tc 允许注入（测试用）；默认自动 detect()。工具链缺失时对应阶段记为 skipped。
    """
    tc = tc or detect()
    skipped: list[str] = []
    paths = write_artifacts(design, out_dir)

    sim: SimResult | None = None
    synth: SynthResult | None = None
    if run_sim:
        if tc.can_simulate:
            sim = run_simulation(tc, paths["rtl"], paths["tb"], out_dir / "sim",
                                 design.project, dump_vcd=dump_vcd)
        else:
            skipped.append("sim (iverilog/vvp 未找到)")
    if run_synth:
        if tc.can_synthesize:
            synth = run_synthesis(tc, paths["rtl"], out_dir / "synth", design.project)
        else:
            skipped.append("synth (yosys 未找到)")

    return VerifyOutcome(design=design, sim=sim, synth=synth, skipped=skipped)

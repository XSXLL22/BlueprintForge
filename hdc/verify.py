"""仿真（Icarus Verilog）与综合（Yosys），并解析/分类结果。"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from hdc.toolchain import Toolchain, env_for


# ---- 仿真 -------------------------------------------------------------------

@dataclass
class SimResult:
    passed: bool
    compile_ok: bool
    checks_failed: int = 0
    compile_output: str = ""
    sim_output: str = ""
    log: str = ""

    @property
    def error_class(self) -> str:
        if self.compile_ok and self.passed:
            return "none"
        if not self.compile_ok:
            return "compile_error"
        return "assertion_failure"


def _parse_fail_count(sim_out: str) -> int:
    return sum(
        1 for line in sim_out.splitlines()
        if line.strip().startswith("CHECK") and ": FAIL" in line
    )


def run_simulation(
    tc: Toolchain,
    rtl: Path,
    tb: Path,
    sim_dir: Path,
    project: str,
    dump_vcd: bool = False,
) -> SimResult:
    """编译并运行仿真，返回结构化结果。"""
    sim_dir.mkdir(parents=True, exist_ok=True)
    vvp_path = sim_dir / "sim.vvp"
    log_path = sim_dir / "sim.log"

    compile_cmd = [tc.iverilog, "-s", f"tb_{project}", "-o", str(vvp_path), str(rtl), str(tb)]
    if dump_vcd:
        compile_cmd.insert(1, "-DDUMP_VCD")

    cp = subprocess.run(compile_cmd, capture_output=True, text=True, env=env_for(tc.iverilog))
    compile_out = (cp.stdout or "") + "\n" + (cp.stderr or "")
    if cp.returncode != 0:
        log_path.write_text("=== compile ===\n" + compile_out, encoding="utf-8")
        return SimResult(
            passed=False, compile_ok=False, checks_failed=-1,
            compile_output=compile_out, log="=== compile ===\n" + compile_out,
        )

    rp = subprocess.run(
        [tc.vvp, str(vvp_path)], capture_output=True, text=True,
        cwd=str(sim_dir), env=env_for(tc.vvp),
    )
    sim_out = (rp.stdout or "") + "\n" + (rp.stderr or "")

    full_log = "=== compile ===\n" + compile_out + "\n=== simulate ===\n" + sim_out
    log_path.write_text(full_log, encoding="utf-8")

    passed = "SIM_RESULT: PASS" in sim_out
    return SimResult(
        passed=passed,
        compile_ok=True,
        checks_failed=_parse_fail_count(sim_out),
        compile_output=compile_out,
        sim_output=sim_out,
        log=full_log,
    )


# ---- 综合 -------------------------------------------------------------------

@dataclass
class SynthResult:
    ok: bool
    log: str = ""
    resource_report: str = ""
    latches_inferred: bool = False
    error_lines: list = field(default_factory=list)

    @property
    def error_class(self) -> str:
        return "none" if self.ok else "synthesis_error"


def run_synthesis(tc: Toolchain, rtl: Path, synth_dir: Path, project: str) -> SynthResult:
    """运行 Yosys 综合，输出网表与资源报告。"""
    synth_dir.mkdir(parents=True, exist_ok=True)
    netlist = synth_dir / f"{project}_netlist.v"
    script = synth_dir / "synth.ys"
    script.write_text(
        f"read_verilog {rtl.as_posix()}\n"
        f"synth -top {project}\n"
        f"stat\n"
        f"write_verilog -noattr {netlist.as_posix()}\n",
        encoding="utf-8",
    )

    cp = subprocess.run(
        [tc.yosys, "-q", "-s", str(script)], capture_output=True, text=True,
        env=env_for(tc.yosys),
    )
    log = (cp.stdout or "") + "\n" + (cp.stderr or "")
    (synth_dir / "synth.log").write_text(log, encoding="utf-8")

    error_lines = [l for l in log.splitlines() if "ERROR" in l]
    ok = cp.returncode == 0 and not error_lines
    latches = re.search(r"latch", log, re.IGNORECASE) is not None

    # 资源报告：截取 stat 段（无则回退到完整日志）
    report = _extract_stat(log) or log
    (synth_dir / "resource_report.txt").write_text(report, encoding="utf-8")

    return SynthResult(
        ok=ok, log=log, resource_report=report,
        latches_inferred=latches, error_lines=error_lines,
    )


def _extract_stat(log: str) -> str:
    """提取 yosys `stat` 输出段（以 `===` 分隔的统计块）。"""
    blocks = re.findall(r"=== [^\n]* ===.*?(?==== |\Z)", log, re.DOTALL)
    for b in blocks:
        if "Number of cells" in b or "Chip area" in b or "Number of wires" in b:
            return b.strip()
    return ""

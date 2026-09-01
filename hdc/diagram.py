"""从 Spec 生成两张 SVG 图纸：模块框图与状态转移图。

纯标准库字符串拼接，无第三方绘图依赖；图纸内容由 Spec 派生，保证与
RTL/tb 同源一致（改 Spec 即改图纸）。
"""
from __future__ import annotations

import math
from pathlib import Path

from hdc.spec import Spec


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---- 模块框图 ---------------------------------------------------------------

def generate_block_diagram(spec: Spec) -> str:
    n = spec.led_count
    led_bus = f"led[{n - 1}:0]" if n > 1 else "led"
    reset = spec.reset_port
    en_ports = ["clk", reset] + (["en"] if spec.enable_port else [])
    inputs_y = [108 + i * 66 for i in range(len(en_ports))]
    out_y = 108 + 66  # 与中间输入对齐

    box = dict(x1=200, y1=58, x2=560, y2=282)

    # 左侧输入端口
    port_rows = []
    for name, y in zip(en_ports, inputs_y):
        port_rows.append(
            f'<line x1="{box["x1"]-40}" y1="{y}" x2="{box["x1"]}" y2="{y}" stroke="#555" stroke-width="1.5"/>'
            f'<circle cx="{box["x1"]}" cy="{y}" r="3" fill="#333"/>'
            f'<text x="{box["x1"]-48}" y="{y+4}" text-anchor="end" font-size="13" font-family="monospace">{_esc(name)}</text>'
        )
    # 右侧输出端口
    port_rows.append(
        f'<line x1="{box["x2"]}" y1="{out_y}" x2="{box["x2"]+40}" y2="{out_y}" stroke="#555" stroke-width="1.5"/>'
        f'<circle cx="{box["x2"]}" cy="{out_y}" r="3" fill="#333"/>'
        f'<text x="{box["x2"]+48}" y="{out_y+4}" text-anchor="start" font-size="13" font-family="monospace">{led_bus}</text>'
    )

    # 内部子块（自上而下：计数器 -> 比较器 -> 移位寄存器）
    sub = [
        ("tick 计数器", "tick + 1（clk 驱动）", 96),
        ("比较器", "tick == DIVIDER-1", 168),
        ("移位寄存器", "led（RESET_LED 初始）", 240),
    ]
    blocks = []
    for label, detail, y in sub:
        blocks.append(
            f'<rect x="238" y="{y-20}" width="150" height="40" rx="4" fill="#e8f0fe" stroke="#3b82f6"/>'
            f'<text x="313" y="{y-4}" text-anchor="middle" font-size="12" font-weight="bold">{label}</text>'
            f'<text x="313" y="{y+11}" text-anchor="middle" font-size="10" fill="#555">{_esc(detail)}</text>'
        )
    # 块间箭头
    blocks.append('<line x1="313" y1="116" x2="313" y2="148" stroke="#3b82f6" marker-end="url(#arrowB)"/>')
    blocks.append('<line x1="313" y1="188" x2="313" y2="220" stroke="#3b82f6" marker-end="url(#arrowB)"/>')
    # 输入 -> 子块（时钟/复位总线示意）
    for y in inputs_y:
        blocks.append(f'<line x1="{box["x1"]}" y1="{y}" x2="238" y2="{y}" stroke="#999" stroke-dasharray="3,3"/>')
    # 移位寄存器 -> 输出
    blocks.append(f'<line x1="388" y1="240" x2="{box["x2"]}" y2="{out_y}" stroke="#555"/>')

    params = (
        f'{spec.freq_mhz:g} MHz · {spec.interval_ms:g} ms · '
        f'DIVIDER={spec.divider} · TICK_MSB={spec.tick_msb} · '
        f'{spec.direction} · wrap={spec.wrap}'
    )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="700" height="330" viewBox="0 0 700 330">
  <defs>
    <marker id="arrowB" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
      <path d="M0,0 L8,4 L0,8 z" fill="#3b82f6"/>
    </marker>
  </defs>
  <rect width="700" height="330" fill="#ffffff"/>
  <text x="350" y="32" text-anchor="middle" font-size="18" font-weight="bold" font-family="monospace">{_esc(spec.project)}</text>
  <text x="350" y="50" text-anchor="middle" font-size="11" fill="#666">{_esc(params)}</text>
  <rect x="{box['x1']}" y="{box['y1']}" width="{box['x2']-box['x1']}" height="{box['y2']-box['y1']}" rx="6" fill="#f8fafc" stroke="#334155" stroke-width="2"/>
  {''.join(port_rows)}
  {''.join(blocks)}
  <text x="180" y="290" text-anchor="end" font-size="10" fill="#999">模块框图 · 由 Spec 生成</text>
</svg>
'''
    return svg


# ---- 状态转移图 -------------------------------------------------------------

def generate_state_diagram(spec: Spec) -> str:
    n = spec.led_count
    reset_int = (1 << (n - 1)) if spec.direction == "left_to_right" else 1
    seq = spec.expected_sequence()
    # 状态 = 复位态 + 移位序列；wrap 时最后一项回到复位态，去重
    states = [reset_int] + seq
    if spec.wrap:
        states = states[:-1]

    spacing = 120
    radius = 30
    y = 96
    x0 = 70
    width = x0 * 2 + (len(states) - 1) * spacing

    cond = "tick==DIV-1"
    if spec.enable_port:
        cond = "en & " + cond

    nodes = []
    edges = []
    for i, s in enumerate(states):
        cx = x0 + i * spacing
        nodes.append(
            f'<circle cx="{cx}" cy="{y}" r="{radius}" fill="#e8f0fe" stroke="#3b82f6" stroke-width="2"/>'
            f'<text x="{cx}" y="{y-6}" text-anchor="middle" font-size="11">S{i}</text>'
            f'<text x="{cx}" y="{y+12}" text-anchor="middle" font-size="12" font-family="monospace">{n}&#39;b{spec.reset_pattern if i == 0 else _bits(s, n)}</text>'
        )
        if i == 0:
            nodes.append(
                f'<text x="{cx}" y="{y-40}" text-anchor="middle" font-size="10" fill="#b45309">复位初值</text>'
            )
        if i < len(states) - 1:
            x2 = x0 + (i + 1) * spacing
            edges.append(
                f'<line x1="{cx+radius}" y1="{y}" x2="{x2-radius}" y2="{y}" stroke="#3b82f6" stroke-width="1.5" marker-end="url(#arrowS)"/>'
                f'<text x="{(cx+x2)/2}" y="{y-12}" text-anchor="middle" font-size="10" fill="#555">{_esc(cond)}</text>'
            )

    # 末端：wrap 回到 S0，否则自环保持
    last_cx = x0 + (len(states) - 1) * spacing
    if spec.wrap and len(states) > 1:
        cy = y + radius + 34
        edges.append(
            f'<path d="M {last_cx} {y+radius} C {last_cx} {cy}, {x0} {cy}, {x0} {y+radius}" '
            f'fill="none" stroke="#16a34a" stroke-width="1.5" marker-end="url(#arrowG)"/>'
            f'<text x="{(last_cx+x0)/2}" y="{cy+12}" text-anchor="middle" font-size="10" fill="#15803d">wrap 回到 S0</text>'
        )
    elif not spec.wrap:
        edges.append(
            f'<path d="M {last_cx+radius-6} {y-radius+6} a {radius} {radius} 0 1 0 14 -14" '
            f'fill="none" stroke="#16a34a" stroke-width="1.5" marker-end="url(#arrowG)"/>'
            f'<text x="{last_cx}" y="{y+radius+20}" text-anchor="middle" font-size="10" fill="#15803d">hold（停在末端）</text>'
        )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="210" viewBox="0 0 {width} 210">
  <defs>
    <marker id="arrowS" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
      <path d="M0,0 L8,4 L0,8 z" fill="#3b82f6"/>
    </marker>
    <marker id="arrowG" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
      <path d="M0,0 L8,4 L0,8 z" fill="#16a34a"/>
    </marker>
  </defs>
  <rect width="{width}" height="210" fill="#ffffff"/>
  <text x="{width/2}" y="30" text-anchor="middle" font-size="15" font-weight="bold" font-family="monospace">{_esc(spec.project)} 状态转移</text>
  <text x="{width/2}" y="46" text-anchor="middle" font-size="10" fill="#666">{_esc(spec.direction)} · led 每次移位一位</text>
  {''.join(nodes)}
  {''.join(edges)}
</svg>
'''
    return svg


def _bits(value: int, n: int) -> str:
    return f"{value:0{n}b}"


# ---- 写出 -------------------------------------------------------------------

def write_diagrams(spec: Spec, diagrams_dir: Path) -> list[Path]:
    diagrams_dir.mkdir(parents=True, exist_ok=True)
    block = diagrams_dir / "block_diagram.svg"
    state = diagrams_dir / "state_diagram.svg"
    block.write_text(generate_block_diagram(spec), encoding="utf-8")
    state.write_text(generate_state_diagram(spec), encoding="utf-8")
    return [block, state]


# ---- 通用图纸（从 design.json 生成，供 LLM 自由设计使用）---------------------

def generate_generic_block_diagram(project: str, interface: dict) -> str:
    """通用模块框图：左侧输入、右侧输出，内容取自 interface。"""
    inputs = [str(x) for x in interface.get("inputs", [])]
    outputs = [str(x) for x in interface.get("outputs", [])]
    n = max(len(inputs), len(outputs), 1)
    row_h = 36
    box_x1, box_x2 = 250, 460
    box_w = box_x2 - box_x1
    top = 64
    box_h = n * row_h + 16
    box_y1, box_y2 = top, top + box_h

    def cy(i: int) -> float:
        return top + 16 + i * row_h + row_h / 2

    parts = []
    for i, name in enumerate(inputs):
        y = cy(i)
        parts.append(
            f'<line x1="{box_x1-46}" y1="{y:.0f}" x2="{box_x1}" y2="{y:.0f}" stroke="#555" stroke-width="1.5"/>'
            f'<circle cx="{box_x1}" cy="{y:.0f}" r="3" fill="#333"/>'
            f'<text x="{box_x1-52}" y="{y+4:.0f}" text-anchor="end" font-size="12" font-family="monospace">{_esc(name)}</text>'
        )
    for i, name in enumerate(outputs):
        y = cy(i)
        parts.append(
            f'<line x1="{box_x2}" y1="{y:.0f}" x2="{box_x2+46}" y2="{y:.0f}" stroke="#555" stroke-width="1.5"/>'
            f'<circle cx="{box_x2}" cy="{y:.0f}" r="3" fill="#333"/>'
            f'<text x="{box_x2+52}" y="{y+4:.0f}" text-anchor="start" font-size="12" font-family="monospace">{_esc(name)}</text>'
        )

    width = 720
    height = box_y2 + 44
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#ffffff"/>
  <text x="{width/2}" y="30" text-anchor="middle" font-size="18" font-weight="bold" font-family="monospace">{_esc(project)}</text>
  <text x="{width/2}" y="46" text-anchor="middle" font-size="10" fill="#999">模块框图（LLM 自由设计）</text>
  <rect x="{box_x1}" y="{box_y1}" width="{box_w}" height="{box_h}" rx="6" fill="#f8fafc" stroke="#334155" stroke-width="2"/>
  {''.join(parts)}
</svg>
'''


def generate_generic_state_diagram(project: str, state_machine: dict) -> str:
    """通用状态转移图：圆形布局，states/transitions/reset_state 由 design.json 提供。"""
    states = [str(s) for s in state_machine.get("states", [])]
    transitions = state_machine.get("transitions", [])
    reset_state = state_machine.get("reset_state")
    reset_state = str(reset_state) if reset_state is not None else ""

    if not states:
        return _empty_svg(project, "state_machine.states 为空，无法绘制状态图")

    n = len(states)
    cx, cy = 260.0, 170.0
    R = 130.0 if n > 1 else 0.0
    radius = 40

    pos: dict[str, tuple[float, float]] = {}
    for i, s in enumerate(states):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        pos[s] = (cx + R * math.cos(ang), cy + R * math.sin(ang))

    nodes, edges = [], []
    for s in states:
        x, y = pos[s]
        is_reset = s == reset_state
        stroke = "#b45309" if is_reset else "#3b82f6"
        sw = 3 if is_reset else 2
        nodes.append(
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{radius}" fill="#e8f0fe" stroke="{stroke}" stroke-width="{sw}"/>'
            f'<text x="{x:.0f}" y="{y+4:.0f}" text-anchor="middle" font-size="12" font-family="monospace">{_esc(s)}</text>'
        )
        if is_reset:
            nodes.append(f'<text x="{x:.0f}" y="{y-radius-8:.0f}" text-anchor="middle" font-size="10" fill="#b45309">复位</text>')

    for t in transitions:
        frm = str(t.get("from", ""))
        to = str(t.get("to", ""))
        label = str(t.get("label") or t.get("cond") or "")
        if frm not in pos or to not in pos:
            continue
        x1, y1 = pos[frm]
        x2, y2 = pos[to]
        if frm == to:
            edges.append(
                f'<path d="M {x1+radius-8:.0f} {y1-radius+6:.0f} a {radius} {radius} 0 1 0 18 -18" '
                f'fill="none" stroke="#16a34a" stroke-width="1.5" marker-end="url(#arrowG)"/>'
            )
            if label:
                edges.append(f'<text x="{x1+radius+6:.0f}" y="{y1-radius-6:.0f}" font-size="10" fill="#15803d">{_esc(label)}</text>')
            continue
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy) or 1.0
        ux, uy = dx / dist, dy / dist
        sx, sy = x1 + ux * radius, y1 + uy * radius
        ex, ey = x2 - ux * radius, y2 - uy * radius
        edges.append(
            f'<line x1="{sx:.0f}" y1="{sy:.0f}" x2="{ex:.0f}" y2="{ey:.0f}" '
            f'stroke="#3b82f6" stroke-width="1.5" marker-end="url(#arrowS)"/>'
        )
        if label:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            edges.append(f'<text x="{mx:.0f}" y="{my-8:.0f}" text-anchor="middle" font-size="10" fill="#555">{_esc(label)}</text>')

    width, height = 520, 340
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <marker id="arrowS" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#3b82f6"/></marker>
    <marker id="arrowG" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#16a34a"/></marker>
  </defs>
  <rect width="{width}" height="{height}" fill="#ffffff"/>
  <text x="{width/2}" y="28" text-anchor="middle" font-size="15" font-weight="bold" font-family="monospace">{_esc(project)} 状态转移</text>
  {''.join(nodes)}
  {''.join(edges)}
</svg>
'''


def _empty_svg(project: str, note: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="520" height="120" viewBox="0 0 520 120">
  <rect width="520" height="120" fill="#ffffff"/>
  <text x="260" y="55" text-anchor="middle" font-size="14" font-family="monospace">{_esc(project)}</text>
  <text x="260" y="80" text-anchor="middle" font-size="11" fill="#999">{_esc(note)}</text>
</svg>
'''


def write_design_diagrams(design_json: dict, diagrams_dir: Path) -> list[Path]:
    """从 design.json（interface + 可选 state_machine）生成通用图纸。"""
    diagrams_dir = Path(diagrams_dir)
    diagrams_dir.mkdir(parents=True, exist_ok=True)
    project = str(design_json.get("project", "design"))
    interface = design_json.get("interface") or {}
    state_machine = design_json.get("state_machine") or {}

    written = []
    block = diagrams_dir / "block_diagram.svg"
    block.write_text(generate_generic_block_diagram(project, interface), encoding="utf-8")
    written.append(block)

    if state_machine.get("states"):
        state = diagrams_dir / "state_diagram.svg"
        state.write_text(generate_generic_state_diagram(project, state_machine), encoding="utf-8")
        written.append(state)
    return written

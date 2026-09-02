# 架构说明

写给要改这份代码的人。用户视角的用法在 [USAGE.md](../USAGE.md)，这里只讲**模块怎么分、
数据怎么流、接口边界在哪**。

## 一、两段流水线，一个交接面

```
需求 ──► [第一段：hdc/]  ──► RTL(.v) ──► [第二段：hdc/pcb/] ──► 嘉立创 ZIP
```

**交接面就是一份 Verilog 文件。** 这个选择带来三件事：

1. 两段可以单独跑、单独测。已有 `.v` 的人直接进第二段（`python -m hdc x.v --pcb`）。
2. 第一段换成任何别的产 RTL 的方式（另一个模型、手写、其它工具），第二段不用改。
3. 第二段收到的是文本，不是内存对象 —— 没有隐式状态可依赖，可复现。

## 二、模块地图

### 第一段：需求 → 可验证的数字逻辑（`hdc/`）

| 模块 | 职责 | 对外接口 | 行数 |
|------|------|---------|------|
| `clarify.py` | 自然语言 → Spec 覆盖字段 + 假设清单 | `clarify()` | 135 |
| `spec.py` | Spec 加载 / 校验 / 派生参数 | `load_spec()`、`RESET_TYPES` | 236 |
| `generate.py` | 模板渲染：Spec → RTL / TB | `generate()` | 166 |
| `llm.py` | provider 抽象 + 硬契约注入 + 有界修复 | `provider_from_config()`、`design_with_fix()` | 299 |
| `design.py` | LLM 产物契约（`Design`）+ 验证编排 | `Design`、`verify_design()`、`write_artifacts()`、`load_artifacts()` | 148 |
| `verify.py` | 跑 iverilog / yosys + 解析 + 错误分类 | `simulate()`、`synthesize()` | 146 |
| `inject.py` | 故意注入缺陷（证明 TB 能检出） | `BUG_TYPES`、`inject()` | 47 |
| `diagram.py` | 模块框图 / 状态转移图（SVG） | `write_design_diagrams()` | 329 |
| `pipeline.py` | 逻辑层闭环编排 + 打包 | `build()` | 165 |
| `toolchain.py` | 定位 iverilog/vvp/yosys + 子进程环境 | `detect()`、`Toolchain`、`env_for()` | 69 |
| `console.py` | 输出流编码兜底 | `use_utf8()` | 43 |
| `__main__.py` | CLI | `main()` | 198 |

### 第二段：数字逻辑 → 电路板（`hdc/pcb/`）

按数据流顺序：

| 模块 | 职责 | 对外接口 | 行数 |
|------|------|---------|------|
| `synth74.py` | yosys → 74 系列门级网表 | `synthesize()` → `Netlist74` | 178 |
| `cells.py` | 芯片知识库：脚位、功能名、电气类型（**手写**） | `spec_for()` → `ChipSpec`、`is_net_alias()` | 290 |
| `pack.py` | 装箱：cell → DIP 芯片 + 引脚号 | `pack()` → `Assembly` | 273 |
| `peripheral.py` | 板级外围：电源/去耦/时钟/复位/LED/排针 | `build_board()` → `Board`、`BoardOptions` | 402 |
| `footprints.py` | 从 KiCad 官方库读封装，原样内嵌 | `load()` → `Footprint`、`place()`、`library_root()` | 326 |
| `schematic.py` | → `.kicad_sch` | `render()`（纯函数）、`write_schematic()` | 394 |
| `router.py` | A\* 迷宫布线 | `route()` → `RouteResult`、`RouteOptions` | 359 |
| `layout.py` | 摆件 + 板框 + 铺铜 → `.kicad_pcb` | `plan_layout()`、`render()`、`write_pcb()`、`fill_zones()` | 435 |
| `manufacture.py` | DRC + Gerber/钻孔/BOM/CPL + 嘉立创转换 + ZIP | `check_drc()`、`export_fabrication()`、`to_jlcpcb_cpl/bom()` | 340 |
| `kicad.py` | 定位 kicad-cli / 封装库 / 自带 python | `find_cli()`、`require_cli()`、`run()`、`run_python()` | 165 |
| `pipeline.py` | 编排 + **送厂判据** | `build_pcb()` → `PcbResult` | 165 |

## 三、数据流：类型接类型

第二段的每一步都有一个具名类型，下一步只吃上一步的输出，不回头读文件：

```
  .v 文件
    │  synth74.synthesize(tc, rtl, project, out_dir)
    ▼
  Netlist74 { cells: [Cell], ports: [Port], net_names, netlist_json, stat }
    │  pack.pack(netlist)                       ← cells.spec_for() 查引脚表
    ▼
  Assembly { chips: [ChipInstance], connections: [PinConn], io, warnings }
    │  peripheral.build_board(assembly)
    ▼
  Board { components: [Component], io, notes, warnings }
    │
    ├─ schematic.render(board) ─────────────► str → .kicad_sch
    │
    └─ layout.plan_layout(board)             ← footprints.load() 读官方库
         ▼
       Layout { placements: [Placement], outline, ... }
         │  router.route(pads, options)
         ▼
       RouteResult { segments, vias, unrouted }
         │  layout.render(...) → str → .kicad_pcb
         │  layout.fill_zones(pcb)  ← 唯一用 pcbnew 的一步
         ▼
       ZoneFill { area, islands }
         │  manufacture.check_drc() / export_fabrication()
         ▼
       DrcReport + Fabrication { gerbers, drill, bom, cpl, pdfs, archive }
         │
         ▼
       PcbResult.ok  ← 送厂判据
```

`Component.pins` 是 `{引脚号: 网络名}`，一路传到底 —— 原理图按它画标签，板图按它连
线，BOM/CPL 按它填位号。**网络名是字符串，不是对象引用**：所以任何一步都可以单独构造
输入做测试，不需要跑上游。

## 四、接口边界（哪里可以替换）

| 边界 | 现在的实现 | 换掉需要动什么 |
|------|-----------|--------------|
| 需求 → Spec | `clarify()` 关键词提取 | 替成 LLM 结构化抽取，返回 `Clarification` 不变，下游无感 |
| LLM provider | `llm.py` 三种（anthropic / openai 兼容 / ollama） | 加一个类，实现 `complete(prompt) -> str` |
| 第一段 ↔ 第二段 | 一份 `.v` 文件 | 什么都不用动 —— 换任何产 RTL 的方式都行 |
| 综合目标库 | `vendor/74xx-liberty/74ac.lib` | 换 Liberty 库 + 补 `cells.py` 的引脚表 |
| 芯片知识 | `cells.CELLS`（手写引脚表） | 加条目即可；未收录会抛 `UnmappedCellError` 而不是静默 |
| 布线器 | `router.route()` A\* | 换 FreeRouting（T2.1）：需要 `.kicad_pcb → .dsn` 与 SES 回灌 |
| 外围电路 | `peripheral.BoardOptions` | 改数据类字段就能改时钟频率、LED 亮度、上拉阻值，不动逻辑 |
| EDA 后端 | KiCad（`kicad.py` 定位，`HDC_KICAD_CLI` 可覆盖） | 板文件生成是纯函数，理论可换；但 DRC/导出依赖 kicad-cli |

**只有一个地方绑死了外部程序的内存模型**：`layout.fill_zones()` 必须用 KiCad 自带的
`python.exe` 调 `pcbnew` 填铺铜。其余步骤都是「拼字符串 / 调命令行」，可测、可 diff。

## 五、送厂判据（`PcbResult.ok`）

这是整条流水线唯一对外的结论。它为真**当且仅当**五件事同时成立：

```python
ok = (not skipped                 # 一步都没跳过
      and not unrouted            # 没有布不通的网络
      and zone_islands == 1       # 地平面是完整一块
      and drc is not None and drc.ok   # DRC 零违规
      and fabrication is not None)     # 制造文件齐全
```

`errors()` 把不满足的原因逐条列出来（网络名、孤岛数、DRC 原文、跳过原因）。

为什么 `zone_islands == 1` 能代表「没有 GND 焊盘被孤立」：KiCad 默认会删掉**不含任何
焊盘**的孤岛，所以留下来的每一块都至少挂着一个焊盘。块数 >1 ⟹ 至少有一组 GND 焊盘与
主平面不连通 ⟹ DRC 会报 `unconnected_items`。反过来 ==1 就是完整。

顺序上有两处不能调换：

1. **铺铜必须在 DRC 之前填** —— 空铺铜会让 DRC 报一堆 `starved_thermal`。
2. **DRC 必须在导出之前跑** —— 送厂的文件应当是检查过的那一版。

## 六、外部工具的调用点

| 步骤 | 调什么 | 怎么调 |
|------|-------|-------|
| 仿真 | `iverilog` + `vvp` | `subprocess`，`-s tb_<project>` |
| 综合检查 | `yosys` | `subprocess`，脚本落盘（可复现） |
| 74HC 综合 | `yosys` | `proc; techmap; dfflibmap -liberty; abc -liberty; write_json` |
| 读封装库 | 无（直接读文件） | `footprints.library_root()` 定位已装 KiCad 的官方库 |
| 铺铜填充 | **KiCad 自带 `python.exe`** | `kicad.run_python()` —— 唯一能 `import pcbnew` 的解释器 |
| DRC | `kicad-cli pcb drc` | `--exit-code-violations`：5 = 有违规，0 = 干净，其它 = 真失败 |
| Gerber / 钻孔 / 贴片 | `kicad-cli pcb export {gerbers,drill,pos}` | — |
| BOM | `kicad-cli sch export bom` | — |
| PDF | `kicad-cli sch export pdf` / `pcb export pdf` | — |

`kicad-cli` 的子命令只有 `{fp, jobset, pcb, sch, sym, version}` —— **没有 `gerber`
子命令**，所以无头环境里没有办法用官方工具读回 Gerber。这是 V3 改成结构自查的原因。

## 七、坐标系（最容易搞错的一处）

| 场景 | Y 轴方向 |
|------|---------|
| `.kicad_pcb` 板文件内部 | 向**下**增长 |
| Gerber 输出 | 向**上**（板文件的 Y 取负） |
| `kicad-cli pcb export pos` 输出 | **已经是 Gerber 系**（Y 已经是负数） |
| `.drl` 钻孔文件 | 与 pos 逐字一致 |
| 嘉立创要的 CPL | Gerber 系 —— 所以 pos 的数值**一个字都不能改** |
| pcbnew 的 `GetPosition()` | 板坐标系（Y 向下）—— 用它写脚本才需要取反 |

任务清单原文写的「Mid Y 取反」是错的（那条经验来自 Altium/Eagle 或来自直接读 pcbnew
板坐标的脚本）。判据在 `tests/test_pcb_manufacture.py`：DIP 封装的原点就是 1 脚，所以
CPL 里每片芯片的坐标必须与钻孔文件里的某个孔**逐位相同** —— 取反了就一个也对不上。

## 八、测试分层

287 项单测（`python -m unittest discover tests`，约 156 s），按「要装什么」分三层：

| 层 | 装饰器 | 覆盖 | 特点 |
|----|-------|------|------|
| 不需要外部工具 | 无 | Spec、生成、注入、图纸、芯片知识库、装箱、外围、格式转换、判据逻辑、输出编码、示例契约 | 秒级，CI 主力 |
| 只需要 yosys | `@skipUnless(TC.can_synthesize)` | 74HC 综合、只出原理图的链路 | — |
| yosys + KiCad | `@skipUnless(... and kicad.find_cli())` | 摆件、布线、铺铜、DRC、Gerber 导出、全链路 | 慢（导出很花时间），整套只跑一次 |

**判定尽量不看实现**，这是刻意的：

- 产物完整性 → 查文件系统（存在且非空），不查函数返回值。
- 元件清单 → 读导出的 CSV，不读 `Board.components`。
- 坐标系 → 拿 `.drl` 的孔位反过来校验 `cpl.csv`，两份都是外部工具产出的。
- Gerber 合法性 → 按 RS-274X 状态机自己走一遍（格式声明、单位、光圈定义、`M02*`）。
- 板框闭合 → 「每个顶点的度数为偶数」，并与 `plan.outline` 对照（Y 取负）。
- 格式转换的输入样本 → **从真机 KiCad 10.0.6 的输出里原样抄的**（连 6 位小数和引号都
  没改），不是自己编的。

示例电路（`examples/counter/*.v`）是**测试的事实来源**：`tests/examples.py` 读文件，
`tests/test_examples.py` 守住它的契约（顶层模块名、`SIM_RESULT: PASS` 标记、`$finish`）。
所以工具代码里一个字节的具体电路都没有，而测试验的就是用户手上那一份文件。

## 九、实测数据（4 位计数器，2026-09-02）

| 项 | 值 |
|----|----|
| 综合出的 cell | 5 个：`74AC273_8x1DFFR` ×4 + `74AC283_1x1ADD4` ×1 |
| 装箱后芯片 | 74HC273 ×1 + 74HC283 ×1（逻辑）+ 74HC14 ×1（时钟外围） |
| 板上元件 | 21 个，全部 THT 直插 |
| 板尺寸 | 98 × 47 mm，双层 |
| 走线 | 109 段，0 个过孔 |
| 铺铜 | B.Cu 整层 GND，3942 mm²，连通块数 1 |
| DRC | 0 违规（线宽 0.25 mm、间距 0.2 mm） |
| 产物 | 7 层 Gerber + `.gbrjob` + `.drl` + BOM + CPL + 2 份 PDF + ZIP |
| 结论 | `OK`（退出码 0） |

## 十、与任务清单的偏差

开工前写的任务清单与实际做法有 **12 处不同**，每一处的原因都记在
[pcb-roadmap.md 的「实施偏差」](./pcb-roadmap.md#实施偏差)。改代码前值得先读那一节 ——
里面几条（Y 不取反、铺铜层走线代价、封装内嵌）是实测踩出来的，看起来反直觉但改回去会
坏。




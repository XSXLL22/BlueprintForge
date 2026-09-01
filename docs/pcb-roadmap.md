# PCB 落地任务清单（数字逻辑 → 嘉立创可打样电路板）

> 本文是「把自动化电路设计变成实际可用的 PCB」的**任务路线图**：列出全部要做的任务、
> 按执行顺序排列，并标注当前状态。技术路线由大模型推理决定，用户已授权自行安排软件。

## 目标

把 hdc 停在「数字逻辑层」的产物（Verilog + 门级网表）继续推进为**嘉立创（JLCPCB）能直接
打样的工程文件**：Gerber + 钻孔 + BOM + 贴片坐标。

## 技术路线（已决策）

**离散 74HC 逻辑门 + KiCad EDA**。理由：最贴合「逻辑设计 → 纯硬件电路板」的本质；成本最低
（双层板几元/5 片）；现有案例（计数器/流水灯）规模匹配；全链路可脚本化、无人工 GUI 步骤。

## 状态图例

- [x] 已完成
- [ ] 待做（按编号顺序执行）

---

## 阶段 0 —— 工具与库准备

| 状态 | 编号 | 任务 | 说明 |
|------|------|------|------|
| [x] | T0.1 | vendor 74xx-liberty 库 | 已 clone 到 `vendor/74xx-liberty/`，取 `74ac.lib`（Liberty 单元库）、`kicad/74xx.lib`+`74xx.dcm`（KiCad 符号库）、`74_*.v`（单元 Verilog 模型）、`synth_74.ys`（参考综合脚本）、`ic_count.py`（参考装箱）、`kicad/parts.py`（参考 SKiDL 建件） |
| [ ] | T0.2 | 安装 KiCad 8 | 含 `kicad-cli`（导出制造文件）与 pcbnew Python 绑定（脚本化布局布线）；Windows 用 `winget install KiCad.KiCad` 或官方安装包 |
| [ ] | T0.3 | 安装 skidl | `pip install skidl`，用于脚本化生成 KiCad 原理图 + 网表 |

## 阶段 1 —— 核心链路（MVP，本轮）

新增 `hdc/pcb/` 包，纯 Python 编排，调用 yosys / skidl / kicad-cli。MVP 案例用**4 位计数器**
（`tests/test_design.py` 已有现成 RTL + 自检 TB），跑通后套用到流水灯/呼吸灯。

| 状态 | 编号 | 任务 | 说明 |
|------|------|------|------|
| [ ] | T1.1 | `hdc/pcb/synth74.py` | 跑 yosys `proc; techmap; dfflibmap -liberty 74ac.lib; abc -liberty 74ac.lib; write_json`，产出 `netlist.json`。复用 `hdc/toolchain.py` 的 `detect()`/`env_for()` |
| [ ] | T1.2 | `hdc/pcb/pack.py` | 解析 `netlist.json` → 统计 cell type → 装箱到 74HC 芯片（如 `74AC00_4x1NAND2` = 4 门/片、`74AC74_2x1DFFSR` = 2 触发器/片）→ 产出**芯片清单**（型号×数量）与**引脚级连接表** |
| [ ] | T1.3 | `hdc/pcb/peripheral.py` | 生成最小板级外围：电源排针、每芯片 0.1µF 去耦电容、时钟（74HC14 + RC 振荡器，插电即用）、复位、IO/LED。纯 Python 数据模型 |
| [ ] | T1.4 | `hdc/pcb/schematic.py` | 用 skidl 把「芯片清单 + 连接表 + 外围」写成原理图（引 KiCad 自带 `74xx`/`Device` 符号库），`generate_netlist` + `generate_kicad_sch_file` |
| [ ] | T1.5 | `hdc/pcb/layout.py` | pcbnew 脚本网格摆件 + 曼哈顿走线 + 板框 + 去耦就近摆放，`SaveBoard`（单位用 `FromMM()`） |
| [ ] | T1.6 | `hdc/pcb/manufacture.py` | `kicad-cli` 导出 gerbers/drill/pos + `sch export bom`，做嘉立创格式转换（表头重命名、Mid Y 取反、mm 单位、Layer top/bottom），打包 `pcb/<project>_jlcpcb.zip` |
| [ ] | T1.7 | CLI `--pcb` 分支 | `hdc/__main__.py` 加 `--pcb`，编排全链路；用计数器跑通端到端，产出嘉立创可上传 ZIP |

## 阶段 2 —— 后续增强（本轮不做）

| 状态 | 编号 | 任务 | 说明 |
|------|------|------|------|
| [ ] | T2.1 | FreeRouting 自动布线 | 自研 `.kicad_pcb → .dsn` 转换器 + SES 回灌（kicad-cli 不支持 .dsn 导出，是当前唯一断点） |
| [ ] | T2.2 | 板级外围增强 | 555 时钟、更完整电源/复位 |
| [ ] | T2.3 | FPGA 路线 | 大规模设计走 yosys + nextpnr + iCE40 |

## 验证与提交

| 状态 | 编号 | 任务 | 说明 |
|------|------|------|------|
| [ ] | V1 | 芯片清单正确性 | 4-bit 计数器综合后芯片数合理（预期 ~74HC74×2 + 74HC86×1 + 外围），门数↔芯片数一致 |
| [ ] | V2 | 产物完整 | `pcb/` 下产出 gerbers（7 层 + 钻孔）、bom.csv、pos.csv、schematic.pdf、board.pdf、`<project>_jlcpcb.zip` |
| [ ] | V3 | Gerber 可打开 | KiCad gerber viewer / `gerbv` 打开无报错，板框闭合 |
| [ ] | V4 | 格式合规 | BOM/CPL 表头与单位符合嘉立创（Y 已取反、mm 后缀） |
| [ ] | V5 | 回归 | `python -m unittest discover tests` 仍全绿（现有 55 项不回归） |
| [ ] | V6 | 提交规范 | 每个优化一个 commit（Conventional Commits + 中文注解） |

## 关键难点

- **装箱算法**（T1.2）：cell → 芯片/引脚分配，同芯片多门正确复用、扇出/驱动检查。
- **pcbnew 布局布线**（T1.5）：网格摆放 + 走线避让，保证过 DRC（线宽/间距 ≥ 0.127mm）。
- **BOM/CPL 格式转换**（T1.6）：Y 取反、mm 单位、表头、Layer 大小写、旋转归一化。
- **外围时钟**（T1.3）：74HC14 RC 振荡器频率与起振。

## 风险

- **KiCad 安装**（~1GB）：需下载安装，可能耗时；安装失败则阶段 0 阻塞（会明确报错提示）。
- **第一版布线质量**：曼哈顿走线可能交叉/不优，但能满足 DRC 出 Gerber；复杂设计留 FreeRouting。
- **74xx-liberty 库覆盖**：若某 RTL 综合出库里没有的 cell 会映射失败 → 扩充库或回退提示。

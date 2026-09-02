# PCB 落地任务清单（数字逻辑 → 嘉立创可打样电路板）

> 本文是「把自动化电路设计变成实际可用的 PCB」的**任务路线图**：列出全部要做的任务、
> 按执行顺序排列，并标注当前状态。技术路线由大模型推理决定，用户已授权自行安排软件。

**当前状态（2026-09-02）**：阶段 0、阶段 1（T1.1–T1.7）与验证 V1–V6 全部完成，4 位
计数器已从 Verilog 一路做到嘉立创可上传的 ZIP。阶段 2 未开工。做法与本清单原文有 12
处不同，见文末「实施偏差」。

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
| [x] | T0.2 | 安装 KiCad | 装的是 **KiCad 10.0.6**（清单原写「KiCad 8」，10 是当前正式版）。用到 `kicad-cli.exe`（封装库、DRC、导出）与自带的 `bin/python.exe`（唯一能 `import pcbnew` 的解释器，只用于铺铜填充）。定位逻辑在 `hdc/pcb/kicad.py`，可用 `HDC_KICAD_CLI` 覆盖 |
| [x] | T0.3 | 安装 skidl | `skidl 2.3.0` 已装（需 `PYTHONUTF8=1`）。**最终链路没有用它** —— T1.4 直接手写 `.kicad_sch`，理由见「实施偏差」。保留安装以便后续换回 |

## 阶段 1 —— 核心链路（MVP，本轮）✅ 已完成

新增 `hdc/pcb/` 包，纯 Python 编排，外部只依赖 yosys 与 kicad-cli（skidl 最终没用上）。
MVP 案例用**4 位计数器**（`tests/test_design.py` 已有现成 RTL + 自检 TB），已端到端跑通。

| 状态 | 编号 | 任务 | 说明 |
|------|------|------|------|
| [x] | T1.1 | `hdc/pcb/synth74.py` | 跑 yosys `proc; techmap; dfflibmap -liberty 74ac.lib; abc -liberty 74ac.lib; write_json`，产出 `netlist.json` + 资源报告。芯片的**引脚表**（脚位、功能名、电气类型）Liberty 库里没有，手写在 `hdc/pcb/cells.py` |
| [x] | T1.2 | `hdc/pcb/pack.py` | 解析 `netlist.json` → 统计 cell type → 装箱到 74HC 芯片 → 产出芯片清单（型号×数量）与引脚级连接表。单焊盘网络（没用到的输出）会警告而不是静默 |
| [x] | T1.3 | `hdc/pcb/peripheral.py` | 生成最小板级外围：电源排针、每芯片 0.1µF 去耦电容、74HC14 + RC 时钟、复位、输入排针、输出 LED + 限流电阻。纯 Python 数据模型 |
| [x] | T1.4 | `hdc/pcb/schematic.py` | **不走 skidl**：直接生成 `.kicad_sch`（自制单单元 DIP 符号 + 标签连线）。理由见「实施偏差」 |
| [x] | T1.5 | `hdc/pcb/layout.py` + `router.py` + `footprints.py` | 货架式摆件（不是均匀网格）+ **A\* 迷宫布线**（不是曼哈顿直连）+ 板框 + 去耦就近 + B.Cu 整层 GND 铺铜。封装从已装 KiCad 的官方库读出后原样内嵌；`.kicad_pcb` 由纯函数渲染，pcbnew 只用来填铺铜 |
| [x] | T1.6 | `hdc/pcb/manufacture.py` | `kicad-cli` 导出 gerbers/drill/pos + `sch export bom` + 两份 PDF，做嘉立创格式转换（表头重命名、mm 后缀、Layer 大小写、旋转 `% 360`），打包 `<project>_jlcpcb.zip`。**「Mid Y 取反」实测是错的**，见「实施偏差」 |
| [x] | T1.7 | CLI `--pcb` 分支 | `hdc/pcb/pipeline.py` 编排全链路，`hdc/__main__.py` 加 `--pcb`（也接受直接给 `.v`）。计数器端到端跑通，产出嘉立创可上传 ZIP |

## 阶段 2 —— 后续增强（本轮不做）

| 状态 | 编号 | 任务 | 说明 |
|------|------|------|------|
| [ ] | T2.1 | FreeRouting 自动布线 | 自研 `.kicad_pcb → .dsn` 转换器 + SES 回灌（kicad-cli 不支持 .dsn 导出，是当前唯一断点） |
| [ ] | T2.2 | 板级外围增强 | 555 时钟、更完整电源/复位 |
| [ ] | T2.3 | FPGA 路线 | 大规模设计走 yosys + nextpnr + iCE40 |

## 验证与提交

| 状态 | 编号 | 任务 | 说明 |
|------|------|------|------|
| [x] | V1 | 芯片清单正确性 | 4 位计数器综合出 **74HC273×1 + 74HC283×1**（预期写的是 74HC74×2 + 74HC86×1）。差别来自 Liberty 库里有宽单元 —— abc 选了 8 位寄存器与 4 位加法器，比离散触发器 + 异或更省片。门数↔芯片数一致由 `tests/test_pcb_pack.py` 判定 |
| [x] | V2 | 产物完整 | `pcb/` 下产出 7 层 Gerber + `.gbrjob` + `<project>.drl`、`bom.csv`、`cpl.csv`、`schematic.pdf`、`board.pdf`、`<project>_jlcpcb.zip`（贴片坐标文件叫 `cpl.csv` 而非清单里的 `pos.csv`） |
| [x] | V3 | Gerber 可打开 | 无头环境没有看图软件可用（`kicad-cli` 没有 gerber 子命令，`gerbview` 只有 GUI），改成按 RS-274X 状态机自查每一层：坐标格式、单位、`M02*` 收尾、有没有选中未定义的光圈 —— 正是看图软件会报错的那类错。板框闭合用「每个顶点度数为偶」判定，并与板文件的规划外框对照 |
| [x] | V4 | 格式合规 | BOM/CPL 表头与单位符合嘉立创，旋转归一到 [0,360)，Layer 首字母大写。**Y 不再取反**（清单写的「Y 已取反」是错的，见「实施偏差」），判据是拿钻孔孔位反过来校验 CPL |
| [x] | V5 | 回归 | `python -m unittest discover -s tests -t .` → **272 项全绿**（123s；清单写的 55 项为本轮开工前的基数） |
| [x] | V6 | 提交规范 | 4 个 commit（Conventional Commits + 中文注解）：铺铜层走线代价修复、T1.6、T1.7、本文更新 |

## 怎么用

```bash
# Spec/RTL 走完逻辑验证后接着做板子
python -m hdc spec.json --pcb --out output

# 直接从一份 .v 起步，只做板子（不再仿真综合）
python -m hdc counter.v --pcb --out output

# 从一句自然语言需求一路做到板子
python -m hdc --design "做个 4 位计数器" --pcb --out output
```

产物在 `pcb/` 子目录下（给 Spec 或 `.v` 时是 `<out>/<project>/pcb/`，`--design` 时是
`<out>/pcb/`）。上传 `<project>_jlcpcb.zip` 到嘉立创，贴片另传 `cpl.csv` + `bom.csv`。
`结论 : OK` 才等于「这套文件可以直接送厂」。

## 实施偏差

清单是开工前写的，下面这些地方实际做法与它不同。每条都记上原因，免得后来人以为
是漏做。

1. **KiCad 装的是 10.0.6，不是 8**（T0.2）：10 是当前正式版。`.kicad_pcb` 写
   v20260206 格式；铺铜填充走它自带的 `bin/python.exe`，那是唯一能 `import pcbnew`
   的解释器。
2. **skidl 装了但没用**（T0.3 / T1.4）：skidl 的强项是生成网表，写 `.kicad_sch`
   要先配好 `fp-lib-table` 与符号库路径（本机上它启动就警告 fp-lib-table 找不到）。
   直接手写 s-expression 反而少一层不确定性 —— 于是 T1.4 自己发 `.kicad_sch`，
   自制单单元 DIP 符号，连接用**标签**而不是画导线（芯片密集时导线不可读，标签也
   是 KiCad 认的连接方式）。skidl 留着，将来要换回不必重装。
3. **芯片引脚表手写在 `hdc/pcb/cells.py`**（T1.1）：Liberty 库只描述逻辑功能与时序，
   没有脚位、封装、引脚功能名。这些是数据手册事实，手写并用单测按手册对照。
4. **多了一个 `hdc/pcb/kicad.py`**：清单里没有。KiCad 的可执行文件、封装库、
   自带 Python 的位置在三个平台上各不相同，集中在一处定位，`HDC_KICAD_CLI` 可覆盖。
5. **摆件是货架式，不是均匀网格**（T1.5）：均匀网格的格子得按最大的 DIP-20 开，
   一颗瓷片电容也占一整格，板子大出两三倍。改成按封装真实宽度往右排、排满换行。
6. **布线是 A\* 迷宫，不是曼哈顿直连**（T1.5）：曼哈顿直连在两片 DIP 之间必然穿过
   别人的焊盘禁区，过不了 DRC。改成格点 A\*，焊盘按「半径 + 间距 + 半线宽」膨胀成
   禁区，顶层堵死才打过孔。
7. **封装原样内嵌，不引用外部库**（T1.5）：DIP 的孔径、阻焊开窗都是有工艺含义的
   尺寸，照抄 KiCad 官方库最可靠。`.kicad_pcb` 里封装是整段内嵌的，于是把库文件
   正文搬过去只改名字/坐标/位号/网络，几何一个字节不动 —— 换台机器打开不缺库。
8. **铺铜层要加走线代价**（T1.5 之后的修复）：B.Cu 整层 GND 铺铜之后，信号如果在
   底层长途奔袭，两条竖线加两条横线就能圈出一块孤岛，落在里面的 GND 焊盘就浮了
   （实测过，DRC 报 `unconnected_items`）。`RouteOptions.layer_cost` 给底层 6 倍
   每格代价；倍率必须 ≥ 1，否则曼哈顿估价不再是 A\* 的下界。`fill_zones()` 顺手
   报连通块个数，`PcbResult.ok` 要求它等于 1。
9. **「Mid Y 取反」是错的**（T1.6 / V4）：`kicad-cli pcb export pos` 给出的已经是
   Gerber 坐标系（Y 轴朝上），与 `.drl` 逐字一致，正是嘉立创要的。再取一次负会把
   整块板上下镜像。那条经验来自 Altium/Eagle，或来自用 pcbnew `GetPosition()` 直接
   读板坐标（Y 轴朝下）的脚本。
10. **贴片坐标文件叫 `cpl.csv`**（T1.6 / V2）：清单写的是 `pos.csv`。嘉立创页面上
    这一栏就叫 CPL，`pos` 是 KiCad 的原始导出名，转换后换成 CPL 避免混淆。
11. **V1 的芯片预期与实际不同**：预期 74HC74×2 + 74HC86×1，实际 74HC273×1 +
    74HC283×1。Liberty 库里有宽单元（8 位寄存器、4 位加法器），abc 选它们更省片。
12. **V3 没有用看图软件**：`kicad-cli` 没有 gerber 子命令，`gerbview` 只有 GUI，
    无头环境跑不了。改成自己按 RS-274X 查一遍每层的格式声明、单位、光圈定义与
    `M02*` 收尾 —— 覆盖的正是看图软件会报错的那类错，但要说明：这不等于人眼看过。

## 关键难点（已解决）

- **装箱算法**（T1.2）：cell → 芯片/引脚分配。宽单元优先、同芯片多门复用；没接线的
  输出会警告而不是静默丢掉。
- **布局布线**（T1.5）：货架摆件 + A\* 迷宫布线，DRC 0 错误（线宽 0.25mm、间距
  0.2mm，都在嘉立创双层板工艺内）。
- **BOM/CPL 格式转换**（T1.6）：表头、mm 后缀、Layer 大小写、旋转 `% 360` 都已按
  实测定下；Y 不取反是这里唯一与清单相反的结论。
- **外围时钟**（T1.3）：74HC14 + RC，元件值已定，实际频率与起振**尚未在真板上验证**
  （本轮没有打样）。

## 风险（现状）

- **KiCad 安装**：已完成（10.0.6）。没装时全链路会明确跳过并给出安装提示，不会假装
  成功。
- **布线质量**：能过 DRC 出 Gerber，但走线不优（A\* 逐网络布，无整体优化）。复杂设计
  仍留给 T2.1 FreeRouting。
- **74xx-liberty 库覆盖**：若某 RTL 综合出库里没有的 cell，`cells.spec_for()` 抛
  `UnmappedCellError`（带可执行的补救提示）而不是静默跳过 → 补 `cells.py` 的引脚表。
- **未打样**：全部结论来自 KiCad 的 DRC 与格式检查，**没有真板验证**。第一次打样前
  建议人工在 KiCad 里过一遍原理图与板图。

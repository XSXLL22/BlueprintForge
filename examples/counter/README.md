# 示例：4 位计数器

本项目的**基准示例**。它不是工具的一部分 —— 工具在仓库的 `hdc/` 下，这里只是一份
输入数据。两者刻意分开：示例可以随便改、随便加，工具的代码里不出现任何具体电路。

## 为什么用它当基准

一个 4 位计数器同时压到了两条链路上最容易出问题的四个地方：

| 特征 | 压到的环节 |
|------|-----------|
| 有时序（寄存器） | yosys 的 `dfflibmap` 要把触发器映射到 74HC 的 D 触发器 |
| 有算术（`+1`） | 综合器要么给出一个 4 位加法器，要么摊成一堆离散门 —— 两种结果芯片数差很多 |
| 有异步复位 | 74HC273 的 `/MR` 引脚必须接对，接错了板子上电就清零不了 |
| 有多位输出 | `count[3:0]` → 板上 4 个 LED，也是布线最密的一处 |

## 文件

| 文件 | 说明 |
|------|------|
| `counter.v` | 设计本体。顶层模块名 `counter`，与项目名一致（综合的 `-top` 靠它） |
| `counter_tb.v` | 自检测试台。顶层 `tb_counter`，全部断言通过时打印 `SIM_RESULT: PASS` |

这两份文件同时是**单测的事实来源** —— `tests/examples.py` 把它们读进去喂给
`tests/test_design.py`（仿真）和 `tests/test_pcb_synth74.py`（综合）。所以改了这里，
跑一遍全量测试。

## 跑法一：只做逻辑仿真

hdc 内部就是这么调 iverilog 的（见 `hdc/verify.py`），可以手动复现：

```bash
cd examples/counter
iverilog -s tb_counter -o sim.vvp counter.v counter_tb.v
vvp sim.vvp
```

真实输出：

```
CHECK reset_zero: PASS
CHECK counts_up: PASS
CHECK counts_up_4: PASS
SIM_RESULT: PASS
```

`SIM_RESULT: PASS` 是 hdc 判定仿真通过的唯一依据 —— 抓的就是这个精确子串。

## 跑法二：从 RTL 一路做到嘉立创可打样文件

```bash
python -m hdc examples/counter/counter.v --pcb --out output
```

这条命令**不再仿真**（RTL 是既成事实），直接进 PCB 链路。真实输出（2026-09-02，
KiCad 10.0.6 + yosys）：

```
[PCB] examples\counter\counter.v → 74HC 分立门电路板

=== counter 电路板 ===
  输出目录: output\counter\pcb
  芯片清单: 74HC273 ×1、74HC283 ×1
  原理图  : counter.kicad_sch
  板图    : counter.kicad_pcb（铺铜 3942mm²，1 块）
  DRC     : 通过
  制造文件: gerber/（8 个）+ counter.drl + bom.csv + cpl.csv
  图纸    : schematic.pdf、board.pdf
  可上传  : counter_jlcpcb.zip
  [警告]  : 网络 N8 只连了一个引脚，多半是没用到的输出
  结论    : OK
```

退出码 `0`。**`结论 : OK` 才等于「这套文件可以直接送厂」**，它要求同时满足：没有跳过
任何步骤、所有网络都布通、铺铜是完整一块、DRC 零违规、制造文件齐全。

那条警告是真的、也是对的：74HC273 是 8 位寄存器，计数器只用掉 4 位，剩下的一个输出
脚没接任何东西，于是它所在的网络只有一个焊盘。工具选择**报出来**而不是静默丢掉。

## 这块板子长什么样

| 项 | 实测值 |
|----|--------|
| 板尺寸 | 98 × 47 mm，双层 |
| 元件 | 21 个（3 片 DIP + 4 LED + 4 限流电阻 + 2 电阻 + 5 电容 + 2 排针 + 1 按钮） |
| 逻辑芯片 | 74HC273 ×1（8 位寄存器，用 4 位）、74HC283 ×1（4 位加法器） |
| 外围芯片 | 74HC14 ×1（施密特反相器，与 R1 100k / C2 1µF 组成张弛振荡器，理论 ≈12.5 Hz） |
| 走线 | 109 段，0 个过孔（这块板顶层就够走） |
| 铺铜 | B.Cu 整层 GND，3942 mm²，连通块数 1 |
| DRC | 0 违规（线宽 0.25 mm、间距 0.2 mm，都在嘉立创双层板工艺内） |

综合报告（`pcb/synth/resource_report_74.txt`）里是 5 个 cell：4 个
`74AC273_8x1DFFR`（触发器位）+ 1 个 `74AC283_1x1ADD4`。装箱后 4 个触发器位落进同
一片 74HC273，所以芯片数是 2 而不是 5。

> 注意：任务清单原先预测的是 74HC74×2 + 74HC86×1。实际不同，因为 Liberty 库里有
> 宽单元（8 位寄存器、4 位加法器），`abc` 选它们更省片。

## 产物在哪

```
output/counter/pcb/
├── counter.kicad_sch          原理图（KiCad 可打开）
├── counter.kicad_pcb          板图
├── counter.kicad_pro          工程文件（双击这个，原理图与板图一起打开）
├── drc.rpt                    DRC 报告（通过与否都留）
├── gerber/                    7 层 Gerber + .gbrjob + counter.drl
├── bom.csv                    嘉立创格式物料清单
├── cpl.csv                    嘉立创格式贴片坐标
├── schematic.pdf / board.pdf  给人看的图纸
├── counter_jlcpcb.zip         ← 上传这个
└── synth/                     netlist.json、综合日志、资源报告、yosys 脚本
```

送厂时上传 `counter_jlcpcb.zip`（里面是 Gerber + 钻孔），贴片另外传 `cpl.csv` 与
`bom.csv`。

## 尚未验证的部分

诚实起点：**这块板没有真正打样过**。所有结论来自 KiCad 的 DRC 与对 Gerber 的格式
自查。具体来说：

- RC 时钟（74HC14 + R1 100k + C2 1µF）**没在真板上测过**。频率是按
  *f* ≈ 1/(0.8·R·C) 算的 ≈12.5 Hz；实际值受 74HC14 的阈值离散性影响，可能差 ±50%。
  板上留了 J2 跳线（`RC` / `CLK` / `EXT`），可以改接外部时钟源绕开这一点。
- Gerber 没有用看图软件人眼看过（无头环境没有可用的看图软件；改成按 RS-274X 逐层
  自查格式、单位、光圈定义与 `M02*` 收尾，见 `tests/test_pcb_manufacture.py`）。
- 第一次打样前建议在 KiCad 里人工过一遍原理图与板图。

## 相关文档

- 工具怎么用：[../../USAGE.md](../../USAGE.md)
- 技术路线与模块划分：[../../docs/architecture.md](../../docs/architecture.md)
- PCB 链路的任务清单与实施偏差：[../../docs/pcb-roadmap.md](../../docs/pcb-roadmap.md)

# BlueprintForge · hdc

**一句话需求 → 数字逻辑 → 可打样的电路板。**

用大模型的逻辑推理能力做电路设计：用户说要什么，模型推理出该用什么逻辑结构、怎么实
现；工具链负责把它验证到底，并一路做成工厂能收的文件。

```
"做个 4 位计数器"  →  Verilog + 自检 TB  →  74HC 门级网表  →  原理图 + 板图  →  嘉立创 ZIP
                        ↑ iverilog 仿真        ↑ yosys 综合       ↑ A* 布线 + DRC    ↑ Gerber/BOM/CPL
```

现在这条链是**通的**：一条命令从 RTL 走到嘉立创可上传的 ZIP，DRC 零违规。但它还没有
真正打样过 —— 下面「已实现 / 未实现」一节把边界写清楚了。

## 它现在能做到哪一步

| 阶段 | 输入 | 输出 | 状态 |
|------|------|------|------|
| 需求澄清 | 一句自然语言 | 补全字段 + 假设清单 | ✅ 关键词提取版（可换 LLM） |
| 逻辑设计（模板） | Spec JSON | RTL + 自检 TB | ✅ 覆盖流水灯一类 |
| 逻辑设计（LLM 自由） | 一句自然语言 | RTL + TB + 状态机 + 设计构想 | ✅ 电路种类不设限 |
| 逻辑验证 | RTL + TB | 仿真日志 + 综合报告 + 分类错误 | ✅ 失败自动反馈重写 ≤3 轮 |
| 离散门综合 | RTL | 74 系列门级网表 | ✅ 限 `74xx-liberty` 收录的单元 |
| 芯片装箱 | 门级网表 | 芯片型号 × 数量 + 引脚级连接表 | ✅ |
| 原理图 | 连接表 | `.kicad_sch` | ✅ 自制 DIP 符号 + 标签连线 |
| 板图 | 连接表 | `.kicad_pcb`（摆件 + 布线 + 地平面） | ✅ 双层，DRC 0 违规 |
| 制造文件 | `.kicad_pcb` | Gerber + 钻孔 + BOM + CPL + ZIP | ✅ 嘉立创格式 |
| 真板验证 | ZIP | 一块能用的板子 | ❌ **没打样过** |

## 30 秒看它跑一遍

```bash
python -m hdc examples/counter/counter.v --pcb --out output
```

真实输出（2026-09-02，KiCad 10.0.6 + yosys，退出码 0）：

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

`结论 : OK` 是这条流水线唯一对外的判据，它同时要求：没跳过任何步骤、所有网络都布通、
地平面是完整一块、DRC 零违规、制造文件齐全。任一不满足就是 `NG`，绝不假装成功。

## 技术路线

整条链分两段，中间以 **RTL（Verilog）** 为交接面。两段可以单独跑：只要逻辑就停在第一
段，已有 `.v` 就直接进第二段。

```
              第一段：需求 → 可验证的数字逻辑
┌───────────────────────────────────────────────────────────┐
│  一句话需求                                                │
│      │                                                     │
│      ├─(A) 澄清 → Spec JSON → 模板渲染 ──┐                 │
│      └─(B) LLM 自由设计 ─────────────────┤                 │
│                                          ▼                 │
│                              RTL(.v) + 自检 TB(.v)         │
│                                          │                 │
│                    ┌─────────────────────┴──────┐          │
│                    ▼                            ▼          │
│            iverilog 仿真                  yosys 综合检查    │
│                    │                            │          │
│                    └────────► 错误分类 ◄────────┘          │
│                                  │                         │
│                        失败 → 反馈 LLM 重写（≤3 轮）       │
└───────────────────────────────────────────────────────────┘
                                   │  RTL 是交接面
              第二段：数字逻辑 → 可打样的电路板
┌──────────────────────────────────┴────────────────────────┐
│  yosys + 74xx Liberty 库 → 74 系列门级网表(netlist.json)   │
│      │                                                     │
│      ▼  装箱：cell → DIP 芯片 + 引脚号                     │
│  芯片清单 + 引脚级连接表                                   │
│      │                                                     │
│      ▼  板级外围：电源/去耦/RC 时钟/复位/LED/排针           │
│  完整板级网表(Board)                                       │
│      │                                                     │
│      ├──► .kicad_sch（原理图，标签连线）                    │
│      └──► .kicad_pcb（货架摆件 → A* 布线 → GND 铺铜）       │
│                   │                                        │
│                   ▼  kicad-cli                             │
│      DRC ─► Gerber ×7 + 钻孔 + BOM + CPL + 两份 PDF        │
│                   │                                        │
│                   ▼  格式转换 + 打包                        │
│           <project>_jlcpcb.zip  ← 上传这个                  │
└───────────────────────────────────────────────────────────┘
```

### 第一段：需求 → 可验证的数字逻辑

两条生成路径，事实来源不同：

- **模板路径**：Spec JSON 是唯一事实来源，渲染模板出 RTL/TB。确定性、可重复、无需
  联网，代价是只覆盖已写模板的电路类别（当前：流水灯）。
- **LLM 自由设计路径**：LLM 的产出就是事实来源，端口、参数、结构完全自由。工具只强制
  **4 条最小硬契约**：RTL 顶层模块名 = 项目名、TB 顶层 = `tb_<项目名>`、TB 打印
  `SIM_RESULT: PASS`、RTL 可综合。

约束少到只有 4 条是刻意的：**设计空间交给模型，正确性交给工具链**。验证不通过时，工具
把错误分类（`compile_error` / `assertion_failure` / `synthesis_error`）连同日志摘要
反馈给模型重写，最多 3 轮，超限如实报失败。

### 第二段：数字逻辑 → 可打样的电路板

选定的路线是**离散 74HC 逻辑门 + KiCad EDA**。三条备选路线与落选理由：

| 路线 | 落选理由 |
|------|---------|
| **74HC 离散门 + KiCad**（采用） | 最贴近「逻辑设计 → 纯硬件电路」的本质；双层板几元/5 片；全链路可脚本化，无人工 GUI 步骤 |
| FPGA（yosys + nextpnr + iCE40） | 板子只是一颗 FPGA + 外围，"逻辑变成硬件"这件事被隐藏在比特流里；规模优势对当前案例用不上（留作 T2.3） |
| CPLD / GAL | 工具链闭源、器件停产居多 |
| 在线 EDA（嘉立创 EDA 等） | 没有可脚本化的无头接口，必须点 GUI |

关键取舍：**能不能全自动**。KiCad 提供 `kicad-cli`（无头导出 + DRC）与可 `import
pcbnew` 的自带 Python，所以从 RTL 到 ZIP 中间可以没有一次鼠标点击 —— 这是这条路线
被选中的决定性原因。

## 实现要点

按「解决了什么真实问题」排，每条都是实测踩出来的：

**1. 芯片引脚表是手写的知识库**（`hdc/pcb/cells.py`）
Liberty 库只描述逻辑功能与时序，**没有脚位、封装、引脚功能名** —— 这些是数据手册事
实。手写成表，用单测按手册逐条对照。库里没收录的 cell 会抛 `UnmappedCellError` 并给出
可执行的补救提示，而不是静默跳过。

**2. 装箱按「宽单元优先」**（`hdc/pcb/pack.py`）
4 位计数器综合出的是 1 个 4 位加法器 + 4 个触发器位，装箱后是 **74HC273×1 +
74HC283×1**（不是离散触发器 + 异或那种 3 片方案）—— abc 在 Liberty 库里选宽单元更省
片。4 个触发器位复用同一片 74HC273。没接线的输出会**警告**，不静默丢弃。

**3. 原理图直接发 s-expression，不用 skidl**（`hdc/pcb/schematic.py`）
skidl 装了但没用上：它要先配好 `fp-lib-table` 与符号库路径（本机上它启动就警告找不
到）。手写 `.kicad_sch` 少一层不确定性。芯片密集时导线画出来不可读，所以连接用**标签**
—— 那也是 KiCad 认的连接方式。

**4. 摆件是货架式，不是均匀网格**（`hdc/pcb/layout.py`）
均匀网格的格子得按最大的 DIP-20 开，一颗瓷片电容也占一整格，板子大出两三倍。改成按封
装真实宽度往右排、排满换行。去耦电容按 `near` 字段贴着它服务的芯片放。

**5. 布线是 A\* 迷宫，不是曼哈顿直连**（`hdc/pcb/router.py`）
曼哈顿直连在两片 DIP 之间必然穿过别人的焊盘禁区，过不了 DRC。改成格点 A\*：焊盘按
「半径 + 间距 + 半线宽」膨胀成禁区，顶层堵死才打过孔。

**6. 底层走线要加代价，否则地平面会被切碎**（`RouteOptions.layer_cost`）
B.Cu 整层 GND 铺铜之后，信号若在底层长途奔袭，两条竖线加两条横线就能圈出一块孤岛，落
在里面的 GND 焊盘就浮了（实测 DRC 报 `unconnected_items`）。给底层 6 倍每格代价即可。
**倍率必须 ≥1**，否则曼哈顿估价不再是 A\* 的下界，最短路不成立。

**7. `zone_islands == 1` 是硬判据**
KiCad 默认会删掉没有焊盘的孤岛，所以铺铜连通块数正好等于 1 ⟺ 没有 GND 焊盘被孤立。
这一条进了送厂判据。

**8. 封装原样内嵌，不引用外部库**（`hdc/pcb/footprints.py`）
DIP 的孔径、阻焊开窗都是有工艺含义的尺寸，照抄 KiCad 官方库最可靠。`.kicad_pcb` 里封
装是整段内嵌的，于是把库文件正文搬过去只改名字/坐标/位号/网络，**几何一个字节不动**
—— 换台机器打开不缺库。

**9. 板文件由纯函数渲染，pcbnew 只用来铺铜**
`.kicad_pcb` 是纯 Python 拼出来的字符串（可测、可 diff、可复现）；只有铺铜填充这一步
必须调 KiCad 自带 `python.exe` 的 `pcbnew`（那是唯一能 `import pcbnew` 的解释器）。

**10. 嘉立创格式转换的四件事**（`hdc/pcb/manufacture.py`）
表头重命名、坐标加 `mm` 后缀、Layer 首字母大写、旋转 `% 360` 归一到 [0,360)。
**Y 不取反** —— `kicad-cli pcb export pos` 给出的已经是 Gerber 坐标系（Y 朝上），与
`.drl` 逐字一致；再取一次负会把整块板上下镜像。判据是拿钻孔孔位反过来校验 CPL：DIP 封
装原点就是 1 脚，CPL 坐标必须与某个孔逐位相同。

**11. ZIP 时间戳写死 1980-01-01**
同样的输入产出字节相同的 ZIP，便于校验与缓存。

**12. 一切判据集中在 `PcbResult.ok`**（`hdc/pcb/pipeline.py`）
跳过了步骤、有网络没布通、铺铜碎成多块、DRC 有违规、制造文件缺失 —— 任一为真即 `NG`，
并把原因逐条列出来。调用方不需要自己拼判断。

## 已实现 / 未实现（如实）

### ✅ 已实现且有测试覆盖

- **逻辑层两条生成路径**：Spec 模板（流水灯）+ LLM 自由设计（不限电路种类）。
- **逻辑验证闭环**：iverilog 仿真（自检 TB 逐项断言）+ yosys 综合检查 + 错误分类 +
  有界修复（≤3 轮）+ 打包（含框图/状态图 SVG）。
- **错误注入验收**：4 种故意注入的缺陷，证明 TB 真的能检出错误（不是空跑通过）。
- **PCB 全链路**：RTL → 74HC 网表 → 装箱 → 板级外围 → 原理图 → 板图 → DRC →
  Gerber/钻孔/BOM/CPL → 嘉立创 ZIP。4 位计数器端到端 `结论 : OK`。
- **回归**：`python -m unittest discover tests` → **287 项全绿**（约 156 s，含
  yosys/iverilog/KiCad 实跑）。缺工具的测试自动跳过而不是假通过。

### ⚠️ 有条件成立（用之前请读这一条）

- **芯片库覆盖有限**：只认 `vendor/74xx-liberty` 收录的单元。综合出库里没有的 cell
  会抛 `UnmappedCellError`（带补救提示），需要补 `hdc/pcb/cells.py` 的引脚表。
- **布线不做整体优化**：逐网络 A\*，先布的占好位置。计数器这种规模能全布通；更复杂的
  设计可能出现布不通的网络 —— 那时会如实报 `unrouted` 并判 `NG`，不会静默交付。
- **Gerber 没有用看图软件人眼看过**：无头环境里没有可用的看图软件（`kicad-cli` 没有
  gerber 子命令，`gerbview` 只有 GUI）。改成按 RS-274X 状态机自查每一层（坐标格式、
  单位、光圈定义、`M02*` 收尾、有没有选中未定义的光圈）—— 覆盖的正是看图软件会报错的
  那类错，**但这不等于人眼看过**。
- **RC 时钟频率是算出来的**：*f* ≈ 1/(0.8·R·C) ≈ 12.5 Hz，受 74HC14 阈值离散性影响可
  能差 ±50%，**没在真板上测过**。板上留了跳线可改接外部时钟。
- **外围是「最小可用」**：电源排针 + 每片 0.1µF 去耦 + 100µF 体电容 + RC 时钟 + 按键
  复位 + LED 限流。没有稳压、没有防反接、没有 ESD 防护。

### ❌ 未实现

| 项 | 现状 | 卡在哪 |
|----|------|--------|
| **真板打样验证** | 从没打过板 | 所有结论都来自 DRC 与格式检查。第一次打样前建议在 KiCad 里人工过一遍图 |
| FreeRouting 自动布线（T2.1） | 未开工 | `kicad-cli` 不支持导出 `.dsn`，要自研 `.kicad_pcb → .dsn` 转换器 + SES 回灌 |
| 更完整的板级外围（T2.2） | 未开工 | 555 时钟、稳压、更完整复位电路 |
| FPGA 路线（T2.3） | 未开工 | 大规模设计走 yosys + nextpnr + iCE40 |
| **通用市售芯片选型** | 未开工 | 项目的下一阶段目标：让模型不只用 74HC，而是从市面在售器件里选型（含 MCU、专用芯片） |
| SMD / 多层板 | 未实现 | 当前全部 THT 直插封装、固定双层（顶层信号 + 底层地平面） |
| 同步复位（模板路径） | 未实现 | 模板路径只支持异步复位（低/高有效）；LLM 路径不受此限 |
| 独立 IR 层 | 有意不做 | 模板路径由 Spec 直接驱动生成 |

## 快速开始

```bash
git clone https://github.com/XSXLL22/BlueprintForge.git
cd BlueprintForge
```

无第三方 Python 依赖，不用建虚拟环境也能跑（纯标准库）。

```bash
# 1) 只做逻辑：模板路径，秒级跑完
python -m hdc specs/led_chaser_fast.json

# 2) 只做逻辑：LLM 自由设计（需配 API；Ollama 本地免 key）
HDC_PROVIDER=ollama python -m hdc --design "做个 4 位计数器"

# 3) 只做板子：已有 .v，直接进 PCB 链路
python -m hdc examples/counter/counter.v --pcb --out output

# 4) 全链路：一句话 → 逻辑验证 → 板子
HDC_PROVIDER=ollama python -m hdc --design "做个 4 位计数器" --pcb --out output
```

退出码：`0` 通过、`1` 验证未通过、`2` 配置/调用/构建错误。
完整的命令、参数、输出目录、故障排查见 **[USAGE.md](./USAGE.md)**。

## 目录结构

```
.
├── hdc/                      # 工具本体（纯标准库）
│   ├── clarify.py            #   需求澄清：自然语言 → Spec 覆盖字段 + 假设
│   ├── spec.py               #   Spec 加载 / 校验 / 派生参数
│   ├── generate.py           #   模板渲染：Spec → RTL / TB
│   ├── llm.py                #   LLM provider 抽象 + 契约注入 + 有界修复
│   ├── design.py             #   LLM 产物契约 + 验证编排
│   ├── verify.py             #   仿真 / 综合 + 结果解析 + 错误分类
│   ├── inject.py             #   故意注入错误（验证 TB 能检出）
│   ├── diagram.py            #   模块框图 / 状态转移图（SVG）
│   ├── pipeline.py           #   逻辑层闭环编排 + 打包
│   ├── console.py            #   输出编码兜底（Windows 管道下的 UTF-8）
│   ├── toolchain.py          #   定位 iverilog / vvp / yosys
│   └── pcb/                  #   ── 第二段：数字逻辑 → 电路板 ──
│       ├── synth74.py        #     yosys → 74 系列门级网表
│       ├── cells.py          #     芯片知识库（脚位/功能名/电气类型，手写）
│       ├── pack.py           #     装箱：cell → DIP 芯片 + 引脚号
│       ├── peripheral.py     #     板级外围：电源/去耦/时钟/复位/LED/排针
│       ├── schematic.py      #     → .kicad_sch
│       ├── footprints.py     #     从 KiCad 官方库读封装，原样内嵌
│       ├── layout.py         #     摆件 + 板框 + 铺铜 → .kicad_pcb
│       ├── router.py         #     A* 迷宫布线
│       ├── manufacture.py    #     Gerber/钻孔/BOM/CPL + 嘉立创格式转换 + ZIP
│       ├── kicad.py          #     定位 kicad-cli / 封装库 / 自带 python
│       └── pipeline.py       #     PCB 链路编排 + 送厂判据（PcbResult）
├── specs/                    # Spec JSON 示例
├── templates/                # 模板路径的 RTL / TB 模板
├── examples/                 # ── 示例与演示，不属于工具 ──
│   └── counter/              #   基准示例：4 位计数器（RTL + 自检 TB + 说明）
├── docs/                     # 技术文档（架构、任务清单、迭代记录）
├── vendor/74xx-liberty/      # 第三方：74 系列 Liberty 库与 KiCad 符号库
└── tests/                    # unittest；缺工具的用例自动跳过
```

**示例与工具是分开的**：`hdc/` 里没有任何具体电路，`examples/counter/*.v` 是纯数据。
单测通过 `tests/examples.py` 读取示例文件，所以测试验的就是用户手上那一份，不是抄件。

## 环境要求

| 要用哪一段 | 需要装什么 | 不装会怎样 |
|-----------|-----------|-----------|
| 全部 | Python ≥3.9（CI 跑 3.11，开发机 3.14 实测；3.9/3.10 未实测） | — |
| 仿真 | [Icarus Verilog](http://iverilog.icarus.com/)（`iverilog` + `vvp`） | 跳过仿真并注明 |
| 综合检查 / 74HC 综合 | [Yosys](https://yosyshq.net/yosys/) | 跳过综合；PCB 链路无法开始 |
| 板图 / 制造文件 | [KiCad](https://www.kicad.org/) ≥8（实测 10.0.6） | 只出原理图，其余步骤记入 `skipped` 并判 `NG` |
| LLM 自由设计 | 一个 LLM API（或本地 Ollama） | `--design` 报配置错误，退出码 2 |

**缺工具时不会假装成功** —— 对应阶段进 `skipped`，结论一定不是 `OK`。
逐平台安装命令见 [USAGE.md 第 2 节](./USAGE.md#2-环境准备)。

## 文档导航

| 文档 | 什么时候看 |
|------|-----------|
| **[USAGE.md](./USAGE.md)** | **技术手册**：安装、每个命令与参数、Spec 字段、输出目录逐项说明、送厂步骤、故障排查、FAQ |
| [docs/architecture.md](./docs/architecture.md) | 想改代码：模块划分、数据流、接口边界、实测数据 |
| [docs/pcb-roadmap.md](./docs/pcb-roadmap.md) | PCB 链路的任务清单、验收项、**12 处实施偏差**及原因 |
| [examples/counter/README.md](./examples/counter/README.md) | 基准示例：怎么跑、真实输出、板子长什么样 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 提交代码：开发环境、测试分层、提交规范 |
| [CHANGELOG.md](./CHANGELOG.md) | 版本变更 |

## 运行测试

```bash
# 全部（缺 iverilog/yosys/KiCad 的用例自动跳过）
python -m unittest discover tests

# 只跑不需要外部工具的部分（秒级）
python -m unittest tests.test_spec tests.test_generate tests.test_inject \
                   tests.test_console tests.test_examples tests.test_pcb_cells
```

测试按「需要装什么」分三层：不需要工具 / 只需要 yosys / 需要 yosys + KiCad。判定尽量
不看实现 —— 产物看文件系统，元件清单看 CSV，坐标系用钻孔文件反过来校验贴片文件。

## 设计原则

- **LLM 是设计大脑，工具链是安全网**：设计空间不设限，正确性由工具兜底。
- **不通过就说不通过**：跳过的步骤、布不通的网络、DRC 违规都如实进结论，没有「大概能用」。
- **判据独立于实现**：验收看产出的文件，不看中间变量。
- **每条与直觉相反的做法都写下原因**：见 [docs/pcb-roadmap.md 的「实施偏差」](./docs/pcb-roadmap.md#实施偏差)。

## 许可

本项目代码 [MIT](./LICENSE)。`vendor/74xx-liberty/` 是 vendored 的第三方库
（[pepijndevos/74xx-liberty](https://github.com/pepijndevos/74xx-liberty)），**上游未随
附 LICENSE 文件**，此处仅作只读参考与综合输入；如需对外分发请自行确认上游授权，详见
[vendor/74xx-liberty/VENDOR.md](./vendor/74xx-liberty/VENDOR.md)。







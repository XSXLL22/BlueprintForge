# hdc 技术手册

本文是 hdc 的**完整使用手册**：安装、每一个命令与参数、Spec 字段、输出目录逐项说明、
送厂步骤、故障排查、二次开发。项目定位与技术路线见 [README](./README.md)，模块划分与
接口边界见 [docs/architecture.md](./docs/architecture.md)。

分三部分：**第一部分**做逻辑（1–9 节），**第二部分**做电路板（10–16 节），**第三部分**
是参数、验收与排查（17–21 节）。只想做板子的人可以从第 10 节开始读。

## 目录

**第一部分 · 逻辑层**
[1 这是什么](#1-这是什么) ·
[2 环境准备](#2-环境准备) ·
[3 快速上手](#3-快速上手) ·
[4 命令行参考](#4-命令行参考) ·
[5 Spec 字段参考](#5-spec-字段参考) ·
[6 输出目录说明](#6-输出目录说明) ·
[7 结果判定与日志](#7-结果判定与日志) ·
[8 自然语言澄清层](#8-自然语言澄清层--demo) ·
[9 LLM 自由设计](#9-llm-自由设计--design)

**第二部分 · 电路板层**
[10 电路板链路总览](#10-电路板链路总览) ·
[11 安装 KiCad](#11-安装-kicad) ·
[12 `--pcb` 怎么用](#12---pcb-怎么用) ·
[13 产物逐项说明](#13-产物逐项说明) ·
[14 送厂：上传到嘉立创](#14-送厂上传到嘉立创) ·
[15 自定义板级参数](#15-自定义板级参数) ·
[16 电路板故障排查](#16-电路板故障排查)

**第三部分 · 参数、验收与排查**
[17 修改设计参数示例](#17-修改设计参数示例) ·
[18 错误注入](#18-错误注入验收自检) ·
[19 常见问题](#19-常见问题faq) ·
[20 二次开发](#20-二次开发) ·
[21 文档索引](#21-文档索引)

---

# 第一部分 · 逻辑层

## 1. 这是什么

hdc 把「做一个数字电路」这件手工活变成一条可重复的流水线。整条链分两段，中间以一份
**Verilog 文件**为交接面：

```
第一段（逻辑层，本文 1–9 节）
  路径一（模板，流水灯专用）：
    自然语言需求 → 澄清 → Spec JSON → 模板生成 RTL/testbench → 仿真 → 综合 → 打包
  路径二（LLM 自由设计，任意电路）：
    自然语言需求 → LLM 生成 RTL/testbench/状态机/构想 → 仿真 → 综合 → 有界修复 → 打包

第二段（电路板层，本文 10–16 节）
  RTL(.v) → 74HC 门级网表 → 装箱成芯片 → 原理图 → 板图 → DRC → 嘉立创可上传 ZIP
```

两段可以单独跑：只要逻辑就停在第一段；已经有 `.v` 就直接进第二段。

- **模板路径**（`python -m hdc specs/led_chaser.json`）：确定性、可重复，只覆盖流水灯。
- **LLM 自由设计路径**（`python -m hdc --design "需求"`）：开发期用 Claude 做设计大脑，
  交付期用户接入自己的 API（Anthropic / OpenAI 兼容 / Ollama）。LLM 自由产出 Verilog +
  状态机 + 结构化描述 + 设计构想，工具链（iverilog 仿真 + yosys 综合）自动验证并**有界修复**
  （把错误分类反馈给模型重写，最多 3 轮），直到通过或轮次耗尽。

核心设计原则：**LLM 是逻辑设计的大脑，工具链是安全网**。设计边界不再人为设限，取决于模型
自身的电子电路知识与逻辑设计能力；验证侧只约定 4 条最小硬契约（见第 9 节），其余端口/参数/
结构完全自由。

---

## 2. 环境准备

第一段只需要 iverilog + yosys；做板子还要 KiCad（见第 11 节）。

### 2.1 前置

- Python 3.9+（`hdc/` 只用标准库，无第三方依赖，无需 `pip install`）
  <br>CI 跑 3.11，开发机 3.14 实测；3.9 / 3.10 未实测。

### 2.2 仿真工具：Icarus Verilog（iverilog + vvp）

| 平台 | 命令 |
|------|------|
| Windows | 管理员终端里 `choco install iverilog -y` |
| Debian/Ubuntu | `sudo apt install iverilog` |
| macOS | `brew install icarus-verilog` |

### 2.3 综合工具：Yosys

Windows 上 yosys 不在 choco 社区源，官方便携版是
[OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build/releases)：

1. 下载解压到某目录，例如 `E:\oss-cad-suite`。
2. 把套件根目录设到环境变量（`hdc` 据此定位 `yosys`）：

   ```powershell
   setx OSS_CAD_SUITE "E:\oss-cad-suite"
   ```

Linux：`sudo apt install yosys`。

### 2.4 验证安装

```bash
iverilog -V
yosys --version
# 或让 hdc 自己报告它找到了什么：
python -c "from hdc.toolchain import detect; print(detect())"
# 做板子还要检查 KiCad：
python -c "from hdc.pcb import kicad; print(kicad.find_cli(), kicad.find_python())"
```

> 未安装工具链也不会报错：hdc 会自动跳过对应阶段，并在报告里注明。**但跳过的运行结论
> 一定不是 `OK`** —— 「跳过」不等于「通过」。

---

## 3. 快速上手

```bash
# ① 端到端演示（一句话需求 → 澄清 → Spec → 完整闭环）
python -m hdc --demo

# ② 从现成 Spec 跑闭环（快速配置，仿真秒级完成）
python -m hdc specs/led_chaser_fast.json

# ③ 从自然语言需求跑闭环（自定义参数）
python -m hdc --demo "8 个灯，500 毫秒换一次，从右往左，到头就停，高电平复位，不带使能，25MHz"

# ④ 用 LLM 从任意需求自动设计（先配好 API，见第 9 节）
HDC_PROVIDER=anthropic HDC_API_KEY=sk-... python -m hdc --design "做一个呼吸灯 PWM 调光"

# ⑤ 从现成 .v 直接做电路板（不再仿真综合，需要 KiCad，见第 11 节）
python -m hdc examples/counter/counter.v --pcb --out output

# ⑥ 一句话 → 逻辑验证 → 电路板（全链路）
HDC_PROVIDER=ollama python -m hdc --design "做个 4 位计数器" --pcb --out output
```

模板路径输出落在 `output/led_chaser/`；LLM 路径落在 `output/<project>/`；电路板产物在
其下的 `pcb/` 子目录。终端末尾打印 `结论 : OK` 即通过。

---

## 4. 命令行参考

```
python -m hdc [spec | file.v | --demo [需求] | --design 需求] [选项]
```

| 参数 | 说明 |
|------|------|
| `spec` | Spec JSON 路径（位置参数）。缺省且未加 `--demo`/`--design` 时报错。 |
| `file.v` | 位置参数给 `.v` / `.sv` 且带 `--pcb` 时：直接从 RTL 做板子，**不再仿真综合**。 |
| `--demo [需求]` | 端到端演示。`需求` 是一句中文；省略时用内置示例。 |
| `--design 需求` | 用接入的 LLM API 从需求自动设计并闭环（见第 9 节）。 |
| `--pcb` | 逻辑验证通过后继续做成 74HC 电路板（见第 12 节）。可与 `spec` / `--design` / `.v` 组合。 |
| `--out DIR` | 输出根目录，默认 `output`。 |
| `--no-sim` | 跳过仿真。 |
| `--no-synth` | 跳过综合。 |
| `--dump` | 仿真时额外导出 VCD 波形到 `sim/waveform.vcd`。 |
| `--inject BUG` | 向 RTL 注入一个错误，验证 testbench 能检出（预期仿真 FAIL）。取值见第 18 节。 |

退出码：

| 码 | 含义 |
|----|------|
| `0` | 通过（带 `--pcb` 时表示板子也做成了且 `结论 : OK`） |
| `1` | 验证未通过：仿真/综合失败，或电路板有布不通的网络 / DRC 违规 / 步骤被跳过 |
| `2` | LLM 配置或调用错误；或电路板构建过程报错（`FabError` / `KicadError`） |

`--pcb` 的一条硬规则：**逻辑没验证通过就不做板子**。此时打印
`[跳过] : 电路板 —— 逻辑没验证通过，先把 RTL 修对再做板子` 并返回 `1`。

---

## 5. Spec 字段参考

Spec JSON 是唯一事实来源。完整示例见 `specs/led_chaser.json`：

```json
{
  "project": "led_chaser",
  "type": "sequential",
  "clock": { "freq_mhz": 50, "reset": "async_active_low" },
  "behavior": {
    "led_count": 4,
    "direction": "left_to_right",
    "interval_ms": 500,
    "wrap": true,
    "enable_port": true,
    "enable_polarity": "active_high"
  },
  "target": "fpga",
  "constraints": { "toolchain": "iverilog+yosys", "max_fix_rounds": 3 }
}
```

| 字段 | 类型 | 默认 | 允许值 / 说明 |
|------|------|------|----------------|
| `project` | string | `led_chaser` | 合法的 Verilog 标识符（用作模块名） |
| `type` | string | `sequential` | 仅作文档用途（MVP 未区分类别） |
| `clock.freq_mhz` | number | `50` | 时钟频率，> 0 |
| `clock.reset` | string | `async_active_low` | `async_active_low` / `async_active_high` |
| `behavior.led_count` | int | `4` | LED 数量，≥ 2 |
| `behavior.direction` | string | `left_to_right` | `left_to_right` / `right_to_left` |
| `behavior.interval_ms` | number | `500` | 切换间隔（毫秒），> 0 |
| `behavior.wrap` | bool | `true` | `true` 循环回到起点；`false` 到头就停 |
| `behavior.enable_port` | bool | `true` | 是否生成 `en` 使能端口 |
| `behavior.enable_polarity` | string | `active_high` | `active_high` / `active_low` |
| `target` | string | `fpga` | 仅作文档用途 |
| `constraints.max_fix_rounds` | int | `3` | 反馈修复层的有界修复轮数上限 |

**派生参数**（无需手填，由 `freq_mhz × 1000 × interval_ms` 算出）：

- `divider` = 每个间隔的时钟周期数；
- `tick_width` / `tick_msb` = 计数器的位宽，由 `divider` 反推。

校验越界（如 `led_count < 2`、`divider` 超 2^31−1）会直接报错并提示当前支持范围。

---

## 6. 输出目录说明

```
output/led_chaser/
├── rtl/led_chaser.v           # 可综合 RTL（纯 Verilog-2001）
├── tb/tb_led_chaser.v         # 自检 testbench（属性断言）
├── sim/sim.log                # 仿真日志（含逐项断言 PASS/FAIL）
├── sim/waveform.vcd           # （--dump 时）波形
├── synth/synth.log            # Yosys 综合日志
├── synth/resource_report.txt  # 资源报告（stat：cells/wires 等）
├── synth/led_chaser_netlist.v # 综合后网表
├── diagrams/block_diagram.svg # 模块框图
├── diagrams/state_diagram.svg # 状态转移图
├── docs/spec.json             # Spec 副本（事实来源）
├── docs/report.md             # 汇总报告（参数/仿真/综合结论）
└── README.md                  # 设计包自述
```

`output*/` 已加入 `.gitignore`，产物可随时重新生成，不入版本库。

带 `--pcb` 时其下还会多一个 `pcb/` 子目录（原理图、板图、Gerber、BOM/CPL、ZIP），
逐项说明见[第 13 节](#13-产物逐项说明)。

---

## 7. 结果判定与日志

**终端结论**：`OK`（仿真且综合都通过）/ `NG`。逐项如下：

- 仿真 `通过`：所有属性断言 PASS；`失败`：注明错误分类（`compile_error` /
  `assertion_failure`）与未通过断言数。
- 综合 `通过`：可综合，无 error；若有锁存器推断会额外警示。

**sim.log** 末尾的关键行：`SIM_RESULT: PASS` / `SIM_RESULT: FAIL`。testbench 断言的属性：

1. `reset_initial_state` — 复位后处于初始位型；
2. `hold_when_disabled` — 使能关闭时状态不变；
3. `interval` — 每次移位精确经过 `DIVIDER` 个周期；
4. `direction` — 每步位型符合期望方向序列；
5. `wrap` / `no_wrap_hold` — 循环回到起点 / 停在远端；
6. `no_unknown` — 结束时 `led` 无 X/Z。

**synth.log**：出现 `ERROR` 即综合失败。**resource_report.txt** 是 `stat` 的资源统计。

**docs/report.md**：参数、仿真、综合的人类可读汇总。

---

## 8. 自然语言澄清层（`--demo`）

澄清层 `hdc/clarify.py` 用**关键词提取 + 默认值兜底**：能识别的字段用提取值，识别不到的
按默认值，并打印「识别 / 假设 / 提示」三类清单。

| 想指定 | 可用的说法示例 |
|--------|----------------|
| LED 数量 | `5 个灯`、`8 个 LED`、`4 路灯` |
| 切换间隔 | `10 毫秒`、`500ms`、`1 秒`、`慢一点`（→1s）、`快一点`（→20ms） |
| 时钟频率 | `50MHz`、`25 兆赫`、`48M` |
| 方向 | `从左往右`、`向右`、`→` / `从右往左`、`向左`、`←` |
| 循环 | `循环`、`循环流动` / `到头就停`、`不循环`、`只跑一遍` |
| 使能 | `带使能`、`可暂停` / `不带使能`、`无使能` |
| 复位 | `低电平复位` / `高电平复位` |

局限：`来回/往复/乒乓`（乒乓模式）MVP 不支持，会提示并退化为循环；`来回` 等歧义表述
由未来 LLM 层做真正的多轮追问。

---

## 9. LLM 自由设计（--design）

`--design` 让 hdc 调用大模型，从一句自然语言需求自动产出完整设计并闭环验证。产物与
模板路径不同，以「LLM 产出」为事实来源：

```
output/<project>/
├── rtl/<project>.v          # LLM 写的可综合 Verilog
├── tb/tb_<project>.v        # LLM 写的自检 testbench
├── design.json              # 结构化描述（interface / state_machine）
├── state_machine.md         # 状态机描述（Mermaid）
├── concept.md               # 逻辑电路设计构想
└── sim/  synth/  diagrams/  # 工具链产出（验证 + 图纸）
```

### 9.1 配置 API

provider / key / model / endpoint 按优先级读取：**环境变量 > `~/.hdc/config`**。

| 环境变量 | 配置文件键 | 说明 |
|----------|-----------|------|
| `HDC_PROVIDER` | `provider` | `anthropic` / `openai` / `ollama` |
| `HDC_API_KEY` | `api_key` | Anthropic 或 OpenAI 兼容服务的 key（Ollama 免） |
| `HDC_MODEL` | `model` | 模型名，如 `claude-sonnet-5` / `gpt-4o-mini` / `llama3.1` |
| `HDC_BASE_URL` | `base_url` | API 地址（默认官方端点；Ollama 默认 `http://localhost:11434`） |

`~/.hdc/config` 是 JSON，例如：

```json
{ "provider": "anthropic", "api_key": "sk-ant-...", "model": "claude-sonnet-5" }
```

### 9.2 三种 provider

```bash
# Anthropic（Claude）
HDC_PROVIDER=anthropic HDC_API_KEY=sk-ant-... python -m hdc --design "做个 4 位计数器"

# OpenAI 兼容（DeepSeek / Kimi / 通义等改 base_url 即可）
HDC_PROVIDER=openai HDC_API_KEY=sk-... python -m hdc --design "做个 PWM 呼吸灯"

# 本地 Ollama（免费、离线，先 ollama pull llama3.1）
HDC_PROVIDER=ollama python -m hdc --design "做个带使能的计数器"
```

### 9.3 最小硬契约

LLM 只需遵守 4 条（其余完全自由，`hdc/llm.py` 已注入 system prompt）：

1. RTL 顶层模块名 = `project`；testbench 顶层模块名 = `tb_<project>`。
2. testbench 结束时打印精确字符串 `SIM_RESULT: PASS` / `SIM_RESULT: FAIL`，`$finish` 在其后。
3. RTL 可综合（Verilog-2001，无 `initial`）。
4. 建议（非强制）：逐条断言打印 `CHECK <name>: PASS/FAIL`。

### 9.4 有界修复

设计未通过时，hdc 把错误分类（`compile_error` / `assertion_failure` / `synthesis_error`）
反馈给模型重写，最多 3 轮。开发期（不接 API）用 `.claude/skills/hdc-design` 让 Claude 走
同一套工作流。

---

---

# 第二部分 · 电路板层

## 10. 电路板链路总览

第二段把一份 `.v` 做成**嘉立创能直接打样的工程文件**。走的是**离散 74HC 逻辑门 +
KiCad** 路线 —— 不是 FPGA，逻辑真的变成板子上一片片芯片和它们之间的走线。

```
 .v  ──yosys+74xx Liberty──►  74 系列门级网表
                                    │
                          装箱：cell → DIP 芯片 + 引脚号
                                    ▼
                         芯片清单 + 引脚级连接表
                                    │
              板级外围：电源 / 每片去耦 / RC 时钟 / 按键复位 / LED / 排针
                                    ▼
                              完整板级网表
                            ┌───────┴───────┐
                            ▼               ▼
                      .kicad_sch        .kicad_pcb
                      （原理图）    货架摆件 → A* 布线 → GND 铺铜
                                            │
                                     kicad-cli DRC
                                            ▼
                    7 层 Gerber + 钻孔 + BOM + CPL + 2 份 PDF
                                            ▼
                             <项目>_jlcpcb.zip   ← 上传这个
```

每一步的产物都落盘，出问题能单步复现。**没有一次鼠标点击** —— 全程命令行。

### 会用到哪些芯片

逻辑芯片由综合器决定，不是预先写死的。以 4 位计数器为例，实际选出的是
**74HC273（8 位寄存器）×1 + 74HC283（4 位加法器）×1** —— 因为 Liberty 库里有这些宽单
元，`abc` 用它们比用离散触发器 + 异或门更省片。

外围固定加：74HC14（施密特反相器，与 RC 组成时钟）、每片 IC 一颗 0.1µF 去耦、100µF 体
电容、按键复位 + 上拉/下拉、每个输出位一颗 LED + 1kΩ 限流、其余输入引到排针。

### 板子的工艺参数

| 项 | 值 | 说明 |
|----|----|------|
| 层数 | 2（顶层信号 + 底层整层 GND 铺铜） | 嘉立创最便宜的档 |
| 线宽 | 0.25 mm | 在嘉立创双层板工艺内 |
| 间距 | 0.2 mm | 同上 |
| 封装 | 全部 THT 直插 | 手焊友好；不支持 SMD |
| 板框 | 自动按摆件包围盒 + 边距 | 计数器实测 98 × 47 mm |

## 11. 安装 KiCad

做板子必须装 KiCad —— 它同时提供三样东西：`kicad-cli`（无头 DRC 与导出）、官方封装库、
以及唯一能 `import pcbnew` 的 Python 解释器（铺铜填充要用）。

| 平台 | 命令 |
|------|------|
| Windows | 从 [kicad.org/download](https://www.kicad.org/download/) 装官方安装包（实测 10.0.6），或 `winget install KiCad.KiCad` |
| Debian/Ubuntu | `sudo apt install kicad`（版本较旧时用官方 PPA 或 Flatpak） |
| macOS | `brew install --cask kicad` |

版本要求：**≥8**（`.kicad_pcb` 的 s-expression 版本），本项目实测在 **10.0.6** 上。

### 定位规则

`hdc/pcb/kicad.py` 按这个顺序找 `kicad-cli`：

1. 环境变量 **`HDC_KICAD_CLI`** —— 直接指向可执行文件（优先级最高，找不到就不再回退）。
2. `PATH` 里的 `kicad-cli` / `kicad-cli.exe`。
3. 各平台常见安装位置，按主版本 `10.0 → 9.0 → 8.0 → 7.0` 依次试：
   - Windows：`%LOCALAPPDATA%\Programs\KiCad\<ver>\bin`、`%PROGRAMFILES%\KiCad\<ver>\bin`
   - Linux：`/usr/bin`、`/usr/local/bin`、`~/.local/bin`
   - macOS：`/Applications/KiCad/KiCad.app/Contents/MacOS`

能 `import pcbnew` 的解释器（环境变量 **`HDC_KICAD_PYTHON`** 可覆盖）：

- **Windows**：只认 KiCad 目录下的 `bin/python.exe`（路径里必须含 `kicad`）——
  `PATH` 上的系统 Python 没有 `pcbnew`，认它只会白报错。
- **Linux / macOS**：包管理器装的 KiCad 会把 `pcbnew` 装进系统 Python，此时直接用当前
  解释器（先 `import pcbnew` 试一下，成功才用）。

装在非标准路径时：

```bash
# Windows（Git Bash）
export HDC_KICAD_CLI="/c/Users/你/AppData/Local/Programs/KiCad/10.0/bin/kicad-cli.exe"
# Linux / macOS
export HDC_KICAD_CLI=/usr/bin/kicad-cli
```

### 检查装好了没

```bash
python -c "from hdc.pcb import kicad; print('cli   :', kicad.find_cli()); print('python:', kicad.find_python())"
```

两个都不是 `None` 才能走完全链路。只有 `cli` 为 `None` 时，`--pcb` 会**只出原理图**，
把布局布线记进 `skipped` 并判 `NG`（附上安装提示），不会假装成功。

## 12. `--pcb` 怎么用

三种入口，取决于你手上有什么：

```bash
# ① 已有 .v：只做板子，不再仿真综合
python -m hdc examples/counter/counter.v --pcb --out output
#   → output/counter/pcb/

# ② 已有 Spec：先跑逻辑闭环，通过后接着做板子
python -m hdc specs/led_chaser_fast.json --pcb --out output
#   → output/led_chaser/pcb/

# ③ 一句话起步：LLM 设计 → 逻辑闭环 → 板子
HDC_PROVIDER=ollama python -m hdc --design "做个 4 位计数器" --pcb --out output
#   → output/pcb/     ← 注意：--design 的板子在 <out>/pcb，不带项目名一层
```

产物位置的差别值得记一下：给 Spec 或 `.v` 时是 `<out>/<project>/pcb/`，`--design` 时是
`<out>/pcb/`。

### 逐步跑完是什么样

```
$ python -m hdc examples/counter/counter.v --pcb --out output

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

### 怎么读这份摘要

| 行 | 含义 | 什么情况下要担心 |
|----|------|----------------|
| 芯片清单 | 型号 × 数量，逻辑芯片由综合器选出 | 数量远超预期 → RTL 里有意外的宽逻辑 |
| 板图 …（铺铜 X mm²，**N 块**） | 地平面面积与**连通块数** | **N ≠ 1 就是问题**：有 GND 焊盘被围在孤岛里 |
| DRC | `通过` 或 `有违规 → drc.rpt` | 有违规时报告一定留在 `drc.rpt` |
| 制造文件 | Gerber 个数应为 **8**（7 层 + 1 个 `.gbrjob`） | 少于 8 → 某层没导出 |
| `[警告]` | 不影响送厂，但值得看一眼 | 单焊盘网络通常是「没用到的输出」，正常 |
| `[错误]` | **拦路问题**，逐条列出原因 | 出现任何一条，结论必为 `NG` |
| 结论 | `OK` = 可以直接送厂 | 见下 |

`结论 : OK` 为真**当且仅当**五件事同时成立：一步都没跳过、没有布不通的网络、地平面是
完整一块、DRC 零违规、制造文件齐全。任一不满足就是 `NG`，`[错误]` 行会说清是哪个。

### 只做一半

调 Python API 时可以中途停下（CLI 没有对应开关）：

```python
from pathlib import Path
from hdc.pcb.pipeline import build_pcb

# 只做到原理图 —— 不需要 KiCad
r = build_pcb(Path("counter.v"), "counter", Path("out"), run_layout=False)

# 做到板图 + DRC 为止，不导出 Gerber
r = build_pcb(Path("counter.v"), "counter", Path("out"), run_manufacture=False)
```

两种情况都会把没做的那一步记进 `r.skipped`，因此 `r.ok` 为 `False` —— **主动停下也算
没走完**，这是刻意的：`ok` 的语义是「可以送厂」，不是「没报错」。

## 13. 产物逐项说明

```
output/counter/pcb/
├── synth/
│   ├── netlist.json              yosys 输出的 74 系列门级网表（JSON）
│   ├── resource_report_74.txt    资源报告：每种 cell 各几个
│   ├── synth74.log               yosys 完整日志
│   └── synth74.ys                实际执行的 yosys 脚本（可复现）
├── counter.kicad_sch             原理图
├── counter.kicad_pcb             板图
├── counter.kicad_pro             工程文件 ← 双击这个，原理图与板图一起打开
├── counter.kicad_prl             KiCad 自己写的本地状态（可忽略）
├── drc.rpt                       DRC 报告（通过与否都留）
├── gerber/
│   ├── counter-F_Cu.gtl          顶层铜箔
│   ├── counter-B_Cu.gbl          底层铜箔（整层 GND 铺铜）
│   ├── counter-F_Silkscreen.gto  顶层丝印
│   ├── counter-B_Silkscreen.gbo  底层丝印
│   ├── counter-F_Mask.gts        顶层阻焊
│   ├── counter-B_Mask.gbs        底层阻焊
│   ├── counter-Edge_Cuts.gm1     板框
│   ├── counter-job.gbrjob        Gerber 作业描述
│   └── counter.drl               钻孔文件（Excellon，METRIC）
├── bom.csv                       物料清单（嘉立创格式）
├── cpl.csv                       贴片坐标（嘉立创格式）
├── schematic.pdf                 原理图 PDF（给人看）
├── board.pdf                     板图 PDF（给人看）
└── counter_jlcpcb.zip            ← 上传这个（内含 Gerber + 钻孔）
```

扩展名用的是 **Protel 传统扩展名**（`.gtl` / `.gbl` / `.gto` / …），因为嘉立创的上传器
就是按扩展名认层的，与 KiCad 的层名拼写无关。

### `bom.csv`

```csv
Comment,Designator,Footprint,Quantity
100uF,C1,CP_Radial_D6.3mm_P2.50mm,1
100nF,"C3,C4,C5",C_Disc_D5.0mm_W2.5mm_P5.00mm,3
74HC273,U1,DIP-20_W7.62mm,1
```

四列表头就是嘉立创要的。位号**逐个列出**（`C3,C4,C5`），不用 `C3-C5` 这种区间缩写 ——
嘉立创看不懂。封装名去掉了 KiCad 的库前缀（`Package_DIP:` 等）。

### `cpl.csv`

```csv
Designator,Val,Package,Mid X,Mid Y,Rotation,Layer
U1,74HC273,DIP-20_W7.62mm,16.5100mm,-17.7800mm,270.00,Top
C1,100uF,CP_Radial_D6.3mm_P2.50mm,96.5200mm,-16.5100mm,0.00,Top
```

三处约定：坐标带 `mm` 后缀；`Rotation` 归一到 `[0, 360)`（KiCad 会把 270° 回读成 −90，
直接交上去就是装反 180°）；`Layer` 首字母大写（`Top` / `Bottom`）。

**Y 是负数，这是对的。** `kicad-cli pcb export pos` 给出的已经是 Gerber 坐标系（Y 轴朝
上），与 `.drl` 逐字一致，正是嘉立创要的。再取一次负会把整块板上下镜像。
校验办法：DIP 封装的原点就是 1 脚，所以 `cpl.csv` 里每片芯片的坐标必须与 `.drl` 里某个
孔**逐位相同**。

### 可复现性

ZIP 里的时间戳写死成 1980-01-01，所以同样的输入产出**字节相同**的 ZIP。`.kicad_pcb` 和
`.kicad_sch` 也都是纯函数拼出来的字符串，可以直接 `diff`。

## 14. 送厂：上传到嘉立创

要上传的只有一个文件：**`<项目>_jlcpcb.zip`**（里面是 7 层 Gerber + `.gbrjob` +
`.drl` 钻孔）。其余产物各有各的用处：

| 文件 | 交给谁 | 说明 |
|------|-------|------|
| `<项目>_jlcpcb.zip` | **嘉立创下单页（Gerber 上传框）** | 板子本身，只传这一个 |
| `bom.csv` | 买件 / 贴片服务 | 嘉立创四列格式，位号逐个列出 |
| `cpl.csv` | 贴片服务 | 见下面关于 THT 的提醒 |
| `schematic.pdf` / `board.pdf` | 人 | 焊接时对照用，不用上传 |
| `drc.rpt` | 自己留档 | 证明这一版检查过 |
| `synth/` | 复现 / 排查 | 网表、日志、yosys 脚本 |

### 顺序

1. **先在 KiCad 里人工过一遍**（这一步别省，理由见本节末）：双击
   `<项目>.kicad_pro` —— 原理图与板图会作为同一个工程一起打开。
2. 打开嘉立创下单页，把 ZIP 拖进 Gerber 上传框，等它解析出预览图。
3. **核对网页解析结果与 CLI 摘要一致**：层数应为 2，板框尺寸应与 `board.pdf` 一致
   （计数器是 98 × 47 mm）。对不上就别下单 —— 说明某层没导出或板框没闭合。
4. 工艺选项按默认即可：2 层、板厚 1.6mm、喷锡。这块板没有阻抗、无铅、沉金之类的
   特殊要求，线宽 0.25mm / 间距 0.2mm 都在最便宜那档的工艺范围内。
5. 贴片是**可选的，而且这块板通常用不上**：板上元件全是 THT 直插封装，机器贴片
   基本只接 SMD。`bom.csv` / `cpl.csv` 生成成嘉立创格式是为了「要用时就能用」，
   实际更常见的用法是拿 `bom.csv` 去买件、拿 `board.pdf` 对着手焊。

### 打样前的人工检查清单

这条链**从没真正打过样**，所有结论都来自 KiCad 的 DRC 与对 Gerber 的格式自查（见
第 13 节）。第一次下单前，在 KiCad 里逐条看一遍这几处 —— 都是自动检查覆盖不到的：

| 看什么 | 怎么看 | 错了会怎样 |
|--------|-------|-----------|
| 复位极性 | 原理图上 `/MR`（或复位网络）常态电平是否正确 | 上电清不了零，或一直被复位 |
| 时钟跳线 | `J2`：短接 1-2 用板载 RC，短接 2-3 由 3 脚输入外部时钟 | 不短接就没有时钟，板子不动 |
| LED 方向 | 阴极（`K`）朝 GND，阳极经限流电阻接输出 | 装反不亮 |
| DIP 缺口方向 | 板图丝印上 1 脚标记与芯片缺口是否一致 | 装反可能烧片 |
| 电源 | `J1`：1 = VCC、2 = GND，接 5V | 反接烧板 |
| 再跑一次 DRC | PCB 编辑器里 `检查` → `设计规则检查` | 与 `drc.rpt` 应当一致 |

原理图上已经写好了每一段外围的接法（电源、时钟频率、复位极性、LED 电流、每个输入排针的
位序）：左上角一块 `※` 开头的说明文字，同样的内容也进了图框的 comment 字段。那些文字是
`Board.notes` 自动生成的，跟着参数变。

## 15. 自定义板级参数

板级参数集中在四个 frozen dataclass 里，**改数据不改逻辑**。CLI 没有对应开关（参数太
多，做成一堆 flag 反而难用），走 Python API：

```python
from pathlib import Path
from hdc.pcb import layout, router
from hdc.pcb.peripheral import BoardOptions
from hdc.pcb.pipeline import build_pcb

r = build_pcb(
    Path("counter.v"), "counter", Path("out"),
    board_options=BoardOptions(
        clock_r_ohm=10_000, clock_c_uf=0.1,   # f ≈ 1/(0.8·R·C) ≈ 1.25 kHz
        led_series_ohm=2_200,                 # 灯暗一点
        add_input_header=False),              # 不要输入排针
    layout_options=layout.LayoutOptions(
        shelf_width=80.0,                     # 板子窄一点，行数变多
        route=router.RouteOptions(
            skip_nets=frozenset({"GND"}),     # ← 别漏，见下
            track_width=0.3, clearance=0.25,
            via_cost=8.0, layer_cost=(1.0, 6.0))),
)
print(r.ok, r.errors())
```

### `BoardOptions`（外围电路，`hdc/pcb/peripheral.py`）

| 字段 | 默认 | 作用 |
|------|------|------|
| `clock_r_ohm` | `100_000` | RC 振荡定时电阻；与 `clock_c_uf` 一起定频率 |
| `clock_c_uf` | `1.0` | 定时电容（µF）。`opts.clock_hz` 直接给出算出来的频率 |
| `led_series_ohm` | `1_000` | LED 限流电阻，越大越暗（5V/2V 压降下约 3mA） |
| `pull_ohm` | `10_000` | 复位上拉/下拉与输入排针下拉的阻值 |
| `decoupling_nf` | `100` | 每片 IC 的去耦电容（nF） |
| `bulk_uf` | `100` | 电源体电容（µF） |
| `add_clock` | `True` | 是否加 74HC14 振荡器 + 时钟源跳线 |
| `add_reset` | `True` | 是否加复位按键 + 上拉/下拉 |
| `add_leds` | `True` | 是否给每个顶层输出位加 LED |
| `add_input_header` | `True` | 是否给时钟/复位之外的输入加排针 + 下拉 |

时钟频率不是配置项而是**算出来的**：`clock_hz = 1/(0.8·R·C)`。默认 100k + 1µF ≈
12.5 Hz —— 肉眼能看清 LED 一位一位跳。想快就减小 R 或 C。

### `LayoutOptions`（摆件与板框，`hdc/pcb/layout.py`）

| 字段 | 默认 | 作用 |
|------|------|------|
| `shelf_width` | `100.0` | 一行排到多宽换行（mm）。板子的大致宽度由它决定 |
| `gap` | `1.27` | 元件之间的横向留白 |
| `shelf_gap` | `2.54` | 行与行之间的留白 |
| `edge_margin` | `2.54` | 元件占地到板框的留白 |
| `copper_inset` | `1.0` | 铜（走线、铺铜）比板框内缩多少。KiCad 要求 ≥0.5 |
| `origin` | `(15.0, 15.0)` | 左上角第一个元件的落点 |
| `route` | 见下 | 嵌在里面的 `RouteOptions` |

### `RouteOptions`（布线，`hdc/pcb/router.py`）

| 字段 | 默认 | LayoutOptions 里的实际值 | 作用 |
|------|------|------------------------|------|
| `grid` | `1.27` | 同 | A\* 的格距（mm）。就是 DIP 的引脚间距 |
| `track_width` | `0.25` | 同 | 线宽 |
| `clearance` | `0.2` | 同 | 最小间距 |
| `via_diameter` / `via_drill` | `0.8` / `0.4` | 同 | 过孔尺寸 |
| `layers` | `2` | 同 | 层数 |
| `via_cost` | `12.0` | **`8.0`** | 换层代价（格）。越大越不愿打过孔 |
| `bend_cost` | `1.0` | 同 | 拐弯代价。只影响美观 |
| `layer_cost` | `()` | **`(1.0, 6.0)`** | 每层走一格的代价倍率 |
| `skip_nets` | `frozenset()` | **`{"GND"}`** | 由铺铜覆盖、不必走线的网络 |
| `margin_cells` | `10` | 同 | 搜索范围在焊盘外框外再放宽几格 |

两条硬约束，破了会出真问题：

- **`layer_cost` 的每一项都不能小于 1。** 小于 1 时曼哈顿估价不再是 A\* 的下界，最短
  路不再成立。底层给 6 倍是为了把长途赶回顶层 —— 底层是整层 GND 铺铜，信号在那儿长途
  奔袭会把地平面圈出孤岛（实测：两条竖线加两条横线就圈死一块），于是 `zone_islands`
  不再是 1，DRC 报 `unconnected_items`。
- **自己传 `RouteOptions` 时别漏 `skip_nets={"GND"}`。** 漏了就会去逐条走 GND，板子瞬
  间挤满，还抢掉信号的通道。`LayoutOptions` 的默认值里带着它，一旦你整个替换 `route`
  就得自己写上。

### `FabOptions`（导出，`hdc/pcb/manufacture.py`）

| 字段 | 默认 | 作用 |
|------|------|------|
| `gerber_layers` | 7 层（`F.Cu` `B.Cu` `F.SilkS` `B.SilkS` `F.Mask` `B.Mask` `Edge.Cuts`） | 导出哪几层 |
| `board_pdf_layers` | `("F.Cu", "B.Cu", "F.SilkS", "Edge.Cuts")` | `board.pdf` 画哪几层 |
| `gerber_dir` | `"gerber"` | Gerber 与钻孔放在 `<out>/<gerber_dir>/`，整目录打包成 ZIP |

## 16. 电路板故障排查

第一条原则：**读那句话**。每个错误都带上了「哪一步、为什么、照做什么」，下面这张表是
按报错原文查的索引。

### 停在综合（还没生成任何板文件）

| 报错 | 原因 | 怎么办 |
|------|------|-------|
| `Synth74Error: 未找到 yosys，无法做 74 系列综合` | 没装或没配 `OSS_CAD_SUITE` | 见第 2.3 节 |
| `Synth74Error: 网表里找不到顶层模块 X（实际有：[...]）` | `.v` 里的模块名与项目名不一致 | 位置参数给 `.v` 时项目名取的是**文件名**（`counter.v` → `counter`），改文件名或改模块名。报错里已经列出实际有哪些模块 |
| `Synth74Error: vendor 74xx-liberty 库缺失` | `vendor/` 没跟着仓库一起拿到 | 重新 clone，或按 `vendor/74xx-liberty/VENDOR.md` 补 |
| `UnmappedCellError: cell X 没有对应的 74 系列芯片规格` | 综合选了一个引脚表里没有的单元 | 在 `hdc/pcb/cells.py` 的 `CELLS` 表按数据手册补一条（型号、封装、电源脚、槽位），或改综合脚本让它映射到已有单元。**不会静默跳过** |

### 停在原理图之后（KiCad 相关）

| 报错 | 原因 | 怎么办 |
|------|------|-------|
| `[跳过] : 布局布线：未找到 kicad-cli` | 没装 KiCad 或不在标准路径 | 装 KiCad，或 `export HDC_KICAD_CLI=...`（第 11 节）。此时**只出原理图**，结论 `NG` |
| `KicadError: 未找到能 import pcbnew 的 Python` | 铺铜那一步找不到 KiCad 自带解释器 | `export HDC_KICAD_PYTHON=".../KiCad/10.0/bin/python.exe"`。Windows 上路径里必须含 `kicad` —— 系统 Python 没有 `pcbnew` |
| `FootprintError: 找不到 KiCad 封装库` | KiCad 装了但库在别处 | `export HDC_KICAD_FOOTPRINTS=` 指向装着 `*.pretty` 的那个目录 |
| `FootprintError: 找不到封装 X：... 不存在` | 库在，但缺这一个封装 | KiCad 安装时勾掉了官方库，重跑安装包补装 |
| `FabError: DRC 没跑起来（退出码 N）` | `kicad-cli pcb drc` 本身挂了（N 不是 0 或 5） | 报错里带 stdout/stderr 原文；多半是 KiCad 版本 <8 或板文件被别的进程占着 |

### 板子做出来了，但结论是 `NG`

`结论 : NG` 时 `[错误]` 行已经逐条说明，对应处理：

| `[错误]` | 含义 | 怎么办 |
|---------|------|-------|
| `网络 X 没布通` | A\* 找不到路 | 加大 `LayoutOptions.shelf_width`（板子宽一点，通道多）、降 `via_cost`（更愿意打过孔）、或让设计小一点。**先布的占好位置**，逐网络布线不做整体优化 |
| `地平面被切成 N 块，有 GND 焊盘落在孤岛里` | 底层走线把铺铜圈断了 | 加大 `layer_cost` 的第二项（默认 6.0），把长途赶回顶层 |
| `DRC：[某条违规]` | KiCad 判的 | 全文在 `drc.rpt`；线宽/间距不满足工艺就调 `RouteOptions` |
| `跳过：制造文件导出：调用方要求做到板图为止` | 自己传了 `run_manufacture=False` | 不是故障。`ok` 的语义是「可以送厂」，主动停下也算没走完 |

`[警告]` 与 `[错误]` 不是一回事：`网络 N8 只连了一个引脚` 这类是警告，不影响送厂
（74HC273 是 8 位寄存器，4 位计数器用不掉的那几位就是这样），结论仍可为 `OK`。

### 产物看着不对

- **Gerber 少于 8 个**：正常是 7 层 + 1 个 `.gbrjob`。少了说明某层没导出，看
  `kicad-cli` 的输出。
- **`cpl.csv` 的 Mid Y 是负数**：这是对的，别改。见第 13 节。
- **ZIP 每次跑出来不一样**：不该发生 —— 时间戳写死成 1980-01-01，同输入应当字节相同。
  真遇到就是 bug。

---

# 第三部分 · 参数、验收与排查

## 17. 修改设计参数示例

只改 Spec JSON，不动任何 HDL：

```jsonc
// 6 个灯，从右往左，100ms，到头停，高复位，不要使能
{ "clock": { "freq_mhz": 50, "reset": "async_active_high" },
  "behavior": { "led_count": 6, "direction": "right_to_left",
                "interval_ms": 100, "wrap": false, "enable_port": false } }
```

保存后跑 `python -m hdc 你的spec.json`。等价做法是 `--demo "6 个灯，100 毫秒，从右往左，到头停，高复位，不要使能"`。

---

## 18. 错误注入（验收自检）

`--inject` 故意往 RTL 塞缺陷，证明验证层真的能抓住 bug（每次都应「仿真 FAIL」）：

| bug | 注入内容 | 由哪项断言捕获 |
|-----|---------|----------------|
| `wrong_direction` | 移位方向取反 | `direction` |
| `wrong_interval` | 分频系数减半 | `interval` |
| `wrong_reset` | 复位初值取反 | `reset_initial_state` |
| `ignore_enable` | 忽略使能信号 | `hold_when_disabled` |

```bash
python -m hdc specs/led_chaser_fast.json --inject wrong_direction   # 预期 NG
```

`--inject` 只对**模板路径**有意义：它按模板 RTL 的文本特征做定点替换（找 `led >> 1`、
`tick == DIVIDER - 1`、`RESET_LED`、`else if (en)`），找不到就抛 `ValueError` 而不是悄悄
放过。LLM 自由设计的 RTL 结构不固定，注入无从下手。

DUT 用注入过的 RTL，testbench 仍由正确的 Spec 生成 —— 这样「FAIL」证明的是 testbench
真的在判，而不是两边一起错。

---

## 19. 常见问题（FAQ）

### 逻辑层

**Q：终端里中文显示成乱码？**
A：Windows 控制台默认 GBK，交互式运行时字形可能显示不全。`chcp 65001` 后再运行即可。
**重定向到文件或管道时不用管** —— `hdc` 启动时会把 `sys.stdout` / `sys.stderr` 钉成
UTF-8（`hdc/console.py`），摘要里的 `mm²` 之类字符不会再让程序在构建成功之后崩掉。

**Q：yosys 报 `error while loading shared libraries: libreadline8.dll`？**
A：便携版 DLL 依赖。确认 `OSS_CAD_SUITE` 指向套件根目录（`hdc` 会把 `lib/` 加进 PATH），
或把套件 `bin/` 直接加入系统 PATH。

**Q：未装 iverilog/yosys，能跑吗？**
A：能。对应阶段自动跳过并注明；纯 Python 单测也照常通过。但**结论一定不是 `OK`**。

**Q：`divider` 超出支持范围报错？**
A：`divider = freq_mhz × 1000 × interval_ms`。过大（> 2^31−1）会溢出 32-bit 计数器。
调小频率或间隔即可。

**Q：仿真为什么慢？**
A：默认 500ms @ 50MHz 要跑 ~1.25 亿周期。日常用 `specs/led_chaser_fast.json`（1ms），
或临时调小 `interval_ms`。

**Q：`--design` 反复修不对，怎么办？**
A：有界修复最多 3 轮（`constraints.max_fix_rounds`），超限如实报失败，不会假装通过。
换个能力更强的模型，或把需求说得更具体（位宽、时钟、复位极性、端口名）。

### 电路板层

**Q：为什么综合出来的芯片跟我预想的不一样？**
A：型号由 `abc` 在 Liberty 库里挑，不是预先写死的。库里有宽单元（8 位寄存器、4 位加法
器）时它会优先用，比离散触发器 + 异或门省片。4 位计数器实测是 74HC273×1 + 74HC283×1，
不是 74HC74×2 + 74HC86×1。

**Q：只有原理图，没有板图和 Gerber？**
A：没找到 `kicad-cli`。这种情况会把布局布线记进 `skipped` 并判 `NG`，摘要里带安装提示。
见第 11 节。

**Q：`--design --pcb` 的板子在哪？**
A：`<out>/pcb/`（不带项目名那一层）。给 Spec 或 `.v` 时是 `<out>/<project>/pcb/`。

**Q：能做 SMD 或四层板吗？**
A：不能。当前封装全是 THT 直插、层数固定为 2（顶层信号 + 底层整层 GND）。

**Q：这块板真的能用吗？**
A：**没打样验证过。** DRC 零违规、Gerber 按 RS-274X 自查通过、CPL 坐标与钻孔逐位对齐 ——
这些都做了，但没有一块实物。第一次下单前请按第 14 节的清单在 KiCad 里人工过一遍。

**Q：RC 时钟频率准不准？**
A：`f ≈ 1/(0.8·R·C)`，默认 ≈12.5 Hz，受 74HC14 阈值离散性影响可能差 ±50%，没在真板上
测过。板上留了 `J2` 跳线可改接外部时钟。

---

## 20. 二次开发

- **LLM 自由设计路径**：核心在 `hdc/llm.py`（provider 抽象 + 契约注入 + 修复闭环）与
  `hdc/design.py`（产物契约 + 验证编排）；开发期设计工作流见 `.claude/skills/hdc-design`。
- **模板路径（流水灯）**：加对应 `templates/*.tpl` 与 `generate.py` 分支，复用 `verify.py` /
  `pipeline.py`。
- **电路板层**：模块划分、数据流与「哪里可以替换」见
  [docs/architecture.md](./docs/architecture.md)。最常动的两处是
  `hdc/pcb/cells.py`（补芯片引脚表）与 `hdc/pcb/peripheral.py`（改外围电路）。
- **示例与工具分开**：`hdc/` 里没有任何具体电路；`examples/counter/*.v` 是纯数据，经
  `tests/examples.py` 喂给单测 —— 所以测试验的就是用户手上那一份文件。改了示例要跑全量
  测试。
- **测试与提交规范**：见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

---

## 21. 文档索引

| 文档 | 内容 |
|------|------|
| [README.md](./README.md) | 项目定位、技术路线、实现要点、已实现/未实现 |
| [USAGE.md](./USAGE.md) | 本文：技术手册（安装、命令、字段、产物、送厂、排查） |
| [docs/architecture.md](./docs/architecture.md) | 模块划分、数据流、接口边界、坐标系、实测数据 |
| [docs/pcb-roadmap.md](./docs/pcb-roadmap.md) | PCB 链路任务清单、验收项、12 处实施偏差及原因 |
| [examples/counter/README.md](./examples/counter/README.md) | 基准示例：怎么跑、真实输出、板子长什么样 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 开发环境、测试分层、提交规范、扩展 |
| [CHANGELOG.md](./CHANGELOG.md) | 版本变更记录 |
| [LICENSE](./LICENSE) | MIT 许可 |

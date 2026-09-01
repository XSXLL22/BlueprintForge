# hdc 使用说明

本文是 hdc（AI 辅助数字硬件设计闭环 MVP）的**使用细则**，比 README 更细，覆盖安装、
命令、Spec 字段、输出解读、常见问题与二次开发。项目定位与架构见 [README](./README.md)。

---

## 1. 这是什么

hdc 把「做一个数字电路」这件手工活变成一条可重复的流水线，支持两条生成路径：

```
路径一（模板，流水灯专用）：
  自然语言需求 → 澄清 → Spec JSON → 模板生成 RTL/testbench → 仿真 → 综合 → 打包

路径二（LLM 自由设计，任意电路）：
  自然语言需求 → LLM 生成 RTL/testbench/状态机/构想 → 仿真 → 综合 → 有界修复 → 打包
```

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

### 2.1 前置

- Python 3.9+（`hdc/` 只用标准库，无第三方依赖，无需 `pip install`）

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
```

> 未安装工具链也不会报错：hdc 会自动跳过对应阶段，并在报告里注明。

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
```

模板路径输出落在 `output/led_chaser/`；LLM 路径落在 `output/<project>/`。终端末尾打印
`结论：OK` 即通过。

---

## 4. 命令行参考

```
python -m hdc [spec | --demo [需求] | --design 需求] [选项]
```

| 参数 | 说明 |
|------|------|
| `spec` | Spec JSON 路径（位置参数）。缺省且未加 `--demo`/`--design` 时报错。 |
| `--demo [需求]` | 端到端演示。`需求` 是一句中文；省略时用内置示例。 |
| `--design 需求` | 用接入的 LLM API 从需求自动设计并闭环（见第 9 节）。 |
| `--out DIR` | 输出根目录，默认 `output`。 |
| `--no-sim` | 跳过仿真。 |
| `--no-synth` | 跳过综合。 |
| `--dump` | 仿真时额外导出 VCD 波形到 `sim/waveform.vcd`。 |
| `--inject BUG` | 向 RTL 注入一个错误，验证 testbench 能检出（预期仿真 FAIL）。取值见第 11 节。 |

退出码：`0` 表示闭环通过，`1` 表示失败，`2` 表示 LLM 配置/调用错误。

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

## 10. 修改设计参数示例

只改 Spec JSON，不动任何 HDL：

```jsonc
// 6 个灯，从右往左，100ms，到头停，高复位，不要使能
{ "clock": { "freq_mhz": 50, "reset": "async_active_high" },
  "behavior": { "led_count": 6, "direction": "right_to_left",
                "interval_ms": 100, "wrap": false, "enable_port": false } }
```

保存后跑 `python -m hdc 你的spec.json`。等价做法是 `--demo "6 个灯，100 毫秒，从右往左，到头停，高复位，不要使能"`。

---

## 11. 错误注入（验收自检）

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

---

## 12. 常见问题（FAQ）

**Q：终端里中文显示成乱码？**
A：Windows 控制台默认 GBK。二选一：先 `chcp 65001` 再运行；或在 Git Bash 里加前缀
`PYTHONIOENCODING=utf-8 python -m hdc ...`。不影响功能与结论。

**Q：yosys 报 `error while loading shared libraries: libreadline8.dll`？**
A：便携版 DLL 依赖。确认 `OSS_CAD_SUITE` 指向套件根目录（`hdc` 会把 `lib/` 加进 PATH），
或把套件 `bin/` 直接加入系统 PATH。

**Q：未装 iverilog/yosys，能跑吗？**
A：能。对应阶段自动跳过并注明；纯 Python 单测也照常通过。

**Q：`divider` 超出支持范围报错？**
A：`divider = freq_mhz × 1000 × interval_ms`。过大（> 2^31−1）会溢出 32-bit 计数器。
调小频率或间隔即可。

**Q：仿真为什么慢？**
A：默认 500ms @ 50MHz 要跑 ~1.25 亿周期。日常用 `specs/led_chaser_fast.json`（1ms），
或临时调小 `interval_ms`。

---

## 13. 二次开发

- **LLM 自由设计路径**：核心在 `hdc/llm.py`（provider 抽象 + 契约注入 + 修复闭环）与
  `hdc/design.py`（产物契约 + 验证编排）；开发期设计工作流见 `.claude/skills/hdc-design`。
- **模板路径（流水灯）**：加对应 `templates/*.tpl` 与 `generate.py` 分支，复用 `verify.py` /
  `pipeline.py`。
- **测试与提交规范**：见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

---

## 14. 文档索引

| 文档 | 内容 |
|------|------|
| [README.md](./README.md) | 项目定位、架构、原则、验收标准 |
| [USAGE.md](./USAGE.md) | 本文：使用细则 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 开发环境、测试、提交规范、扩展 |
| [CHANGELOG.md](./CHANGELOG.md) | 版本变更记录 |
| [LICENSE](./LICENSE) | MIT 许可 |

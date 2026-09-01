# hdc — AI 辅助数字硬件设计闭环（MVP）

从自然语言需求出发，自动完成**逻辑设计 → 仿真验证 → 综合检查 → 有界修复 → 打包**，
输出可进入 EDA 流程的数字逻辑设计包。生成侧有两条路径：

- **模板路径**：Spec JSON 驱动模板生成，确定性、可重复（当前覆盖**流水灯**）。
- **LLM 自由设计路径**：接入大模型（开发期用 Claude，交付期用户接自己的 API）自由产出
  Verilog + 状态机 + 结构化描述 + 设计构想，工具链自动验证并修复，**任意电路皆可**。

> 设计边界不再人为设限，取决于模型的电子电路知识与逻辑设计能力；验证侧只约定 4 条最小硬契约。

## 文档导航

| 文档 | 内容 |
|------|------|
| [USAGE.md](./USAGE.md) | **使用说明**：安装、命令、Spec 字段、输出解读、FAQ |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 贡献指南：开发环境、测试、提交规范、扩展 |
| [CHANGELOG.md](./CHANGELOG.md) | 版本变更记录 |
| [LICENSE](./LICENSE) | MIT 许可 |

## 核心原则

- **LLM 是设计大脑，工具链是安全网**：LLM 自由产出 RTL/testbench/状态机/构想，工具链验证兜底。
- **两条生成路径**：模板路径以 Spec JSON 为唯一事实来源；LLM 路径以「LLM 产出」为事实来源。
- **验证基于属性断言**：不只“仿真跑通”，testbench 内核对复位态、使能保持、方向、
  间隔、wrap、无 X/Z 等属性逐项断言。
- **综合检查可综合**：Yosys 综合无 error 即通过，并产出资源报告。
- **错误分类 + 有界修复**：错误分类后最多修复 `max_fix_rounds` 轮，超限回退保守方案。

## 目录结构

```
.
├── specs/                    # Spec JSON（示例 + 快速测试配置）
│   ├── led_chaser.json       #   默认参数（50MHz / 500ms / 4 LED）
│   └── led_chaser_fast.json  #   快速配置（interval=1ms，仿真秒级跑完）
├── templates/                # 生成模板（占位符 {{ key }}）
│   ├── led_chaser.v.tpl      #   可综合 RTL 模板
│   └── tb_led_chaser.v.tpl   #   自检 testbench 模板
├── hdc/                      # Python 工具链（纯标准库，无第三方依赖）
│   ├── clarify.py            #   需求澄清层：自然语言 -> Spec 覆盖字段 + 假设
│   ├── spec.py               #   Spec 加载 / 校验 / 派生参数
│   ├── generate.py           #   模板渲染：Spec -> RTL / tb
│   ├── toolchain.py          #   检测 iverilog/vvp/yosys
│   ├── inject.py             #   故意注入错误（验证 testbench 能检出）
│   ├── verify.py             #   仿真 / 综合 + 结果解析 / 错误分类
│   ├── diagram.py            #   生成模块框图 / 状态转移图（SVG）
│   ├── pipeline.py           #   闭环编排 + 打包输出
│   ├── demo.py               #   端到端演示
│   ├── design.py             #   LLM 自由设计产物契约 + 验证编排
│   ├── llm.py                #   LLM provider 抽象 + 契约注入 + 有界修复
│   └── __main__.py           #   命令行入口
├── examples/                 #   LLM 自由设计演示脚本（呼吸灯 PWM 等）
├── .claude/skills/hdc-design/  # 开发期用 Claude 做逻辑设计的标准工作流
└── tests/                    # unittest（不依赖模拟器的部分可直接跑）
```

## 环境要求

- Python 3.9+
- [Icarus Verilog](http://iverilog.icarus.com/)（`iverilog` + `vvp`）—— 仿真
- [Yosys](https://yosyshq.net/yosys/)（`yosys`）—— 综合检查

Windows 安装：

```powershell
# iverilog（需要管理员终端）
choco install iverilog -y
```

yosys 不在 choco 社区源里，官方 Windows 分发是
[OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build/releases)（便携版，免安装）。
解压后，把套件根目录设到环境变量 `OSS_CAD_SUITE`（`hdc` 会据此定位 `yosys`），
或直接把 `bin/` 加入 `PATH`：

```powershell
setx OSS_CAD_SUITE "E:\oss-cad-suite"
```

工具未安装时，`hdc` 会自动跳过对应阶段并在报告里注明，不会报错。

## 端到端演示

一句自然语言需求，走完整闭环（澄清 → Spec → 生成 → 仿真 → 综合 → 打包）：

```bash
# 内置需求：5 个灯，10ms 换一次，从左往右循环
python -m hdc --demo

# 自定义需求
python -m hdc --demo "8 个灯，500 毫秒换一次，从右往左，到头就停，高电平复位，不带使能，25MHz"
```

澄清层（`hdc/clarify.py`）用**确定性关键词提取 + 默认值兜底**：能识别的字段用提取值
（LED 数、间隔、方向、频率、循环、使能、复位），识别不到的按默认值并打印「假设」清单，
等价于有界澄清在没有 LLM 时的退化实现。生产环境把 `clarify()` 内部规则替换为 LLM 结构化
抽取即可，下游接口不变。

## LLM 自由设计（--design）

接入大模型，从一句自然语言需求自动产出任意电路设计并闭环验证：

```bash
# Anthropic（Claude）
HDC_PROVIDER=anthropic HDC_API_KEY=sk-ant-... python -m hdc --design "做个 PWM 呼吸灯"

# 本地 Ollama（免费、离线，先 ollama pull llama3.1）
HDC_PROVIDER=ollama python -m hdc --design "做个 4 位计数器"
```

LLM 只需遵守 4 条最小硬契约（RTL 顶层 = `project`、TB 顶层 = `tb_project`、TB 打印
`SIM_RESULT: PASS`、RTL 可综合），其余端口/参数/结构完全自由；未通过时自动把错误分类
反馈给模型重写，最多 3 轮。产物落在 `output/<project>/`（`rtl/`、`tb/`、`design.json`、
`state_machine.md`、`concept.md`、`sim/`、`synth/`、`diagrams/`）。配置与三种 provider
详见 [USAGE.md 第 9 节](./USAGE.md#9-llm-自由设计--design)。

开发期（不接 API）用 `.claude/skills/hdc-design` 让 Claude 走同一套设计工作流，参考
`examples/demo_llm_design.py`（呼吸灯 PWM 调光，完整闭环）。

## 快速开始

```bash
# 生成 + 仿真 + 综合 + 打包（用快速配置，秒级跑完）
python -m hdc specs/led_chaser_fast.json

# 用默认 500ms 配置（仿真约 1.25 亿时钟周期，需要几十秒到几分钟）
python -m hdc specs/led_chaser.json

# 额外导出 VCD 波形到 sim/waveform.vcd
python -m hdc specs/led_chaser_fast.json --dump

# 向 RTL 注入一个错误，验证 testbench 能检出（预期「仿真失败」）
python -m hdc specs/led_chaser_fast.json --inject wrong_direction
```

输出落在 `output/<project>/`：

```
output/led_chaser/
├── rtl/led_chaser.v          # 可综合 RTL
├── tb/tb_led_chaser.v        # 自检 testbench
├── sim/sim.log               # 仿真日志（含逐项断言 PASS/FAIL）
├── sim/waveform.vcd          # （--dump 时）波形
├── synth/synth.log           # Yosys 综合日志
├── synth/resource_report.txt # 资源报告（stat）
├── diagrams/block_diagram.svg  # 模块框图
├── diagrams/state_diagram.svg  # 状态转移图
├── docs/spec.json            # Spec 副本（事实来源）
├── docs/report.md            # 汇总报告
└── README.md
```

## 修改设计参数

编辑 Spec JSON 即可，无需改任何 HDL：

| 字段 | 默认 | 说明 |
|------|------|------|
| `behavior.led_count` | 4 | LED 数量（≥2） |
| `clock.freq_mhz` | 50 | 时钟频率 |
| `behavior.interval_ms` | 500 | 切换间隔 |
| `behavior.direction` | `left_to_right` | 流水方向：`left_to_right` / `right_to_left` |
| `behavior.wrap` | true | 是否循环 |
| `behavior.enable_port` | true | 是否带使能端口 |
| `behavior.enable_polarity` | `active_high` | 使能极性 |
| `clock.reset` | `async_active_low` | 复位类型：`async_active_low` / `async_active_high` |

MVP 范围外的需求（如同步复位、更多复位类型）会友好降级：校验报错并提示当前仅支持的值。

## 验证属性（testbench 断言）

生成的 testbench 对以下属性逐项断言，任何一项失败都会输出 `SIM_RESULT: FAIL`：

1. `reset_initial_state` — 复位后 LED 处于初始位型；
2. `hold_when_disabled` — 使能关闭时状态保持不变；
3. `interval` — 每次移位精确经过 `DIVIDER` 个时钟周期；
4. `direction` — 每步位型符合期望方向序列；
5. `wrap` / `no_wrap_hold` — 循环回到起点，或不循环时停在远端；
6. `no_unknown` — 仿真结束时 `led` 无 X/Z。

## 错误注入（验收用）

`--inject` 支持四种故意注入的缺陷，用于证明验证层能检出错误：

| bug | 注入内容 | 预期被哪项断言捕获 |
|-----|---------|------------------|
| `wrong_direction` | 移位方向取反 | `direction` |
| `wrong_interval` | 分频系数减半 | `interval` |
| `wrong_reset` | 复位初值取反 | `reset_initial_state` |
| `ignore_enable` | 忽略使能信号 | `hold_when_disabled` |

## 运行测试

```bash
# 不依赖模拟器的部分（Spec / 生成 / 注入）
python -m unittest tests.test_spec tests.test_generate tests.test_inject

# 全部（含端到端 + 错误注入；未装 iverilog/yosys 时自动跳过）
python -m unittest discover tests
```

## MVP 验收标准对照

- [x] 模糊需求 ≤3 轮追问内生成正确流水灯（澄清层以关键词提取 + 默认值兜底实现，见 `--demo`）
- [x] 自动仿真通过，testbench 能检出至少一个故意注入的错误
- [x] Yosys 综合无 error（工具链安装后）
- [x] 模块框图 / 状态转移图（`diagrams/`，随 Spec 参数自动生成 SVG）
- [x] 可修改 LED 数量、时钟频率、间隔、方向等参数
- [x] 输出 HDL + testbench + 报告（+ 图纸待补）
- [x] 全程可重复运行，无需人工改代码

## 已知限制（MVP 有意为之）

- 模板路径仅覆盖流水灯一种设计类别（`type` 字段当前只作文档用途）；LLM 路径不设限。
- 模板路径复位仅支持异步（低/高有效）；同步复位暂未实现。
- 不实现独立 IR 层，模板路径由 Spec 直接驱动生成。
- LLM 路径依赖模型自身能力，且需用户配置 API key（见 USAGE 第 9 节）。

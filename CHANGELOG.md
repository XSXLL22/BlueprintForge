# Changelog

本项目的显著变更都会记录在此。格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.2.0] - 2026-09-01

架构升级：生成侧从「模板填充」升级为「LLM 自由生成 + 工具链验证」。设计边界不再人为设限，
取决于模型自身的电子电路知识与逻辑设计能力；验证侧只约定 4 条最小硬契约。

### 新增

- 设计产物契约（`hdc/design.py`）：`Design` + `write_artifacts`/`load_artifacts` +
  `verify_design`（复用仿真/综合做单轮验证，返回 `VerifyOutcome` 错误分类）。
- LLM provider 抽象（`hdc/llm.py`）：Anthropic / OpenAI 兼容 / Ollama 三种 provider，
  纯标准库；配置读 `HDC_*` 环境变量或 `~/.hdc/config`。
- 契约注入 + 有界修复：`SYSTEM_PROMPT` 注入 4 条最小硬契约与 JSON 输出格式；
  `design_with_fix` 把错误分类反馈给模型重写（默认 3 轮）。
- `--design` 命令（`hdc/__main__.py`）：`python -m hdc --design "需求"` 端到端自动设计闭环。
- 通用图纸（`hdc/diagram.py`）：从 `design.json`（interface + state_machine）生成模块框图
  与状态转移图，取代流水灯专用硬编码。
- 开发期设计工作流（`.claude/skills/hdc-design/`）：用 Claude 做逻辑设计的标准流程。
- 演示脚本（`examples/demo_llm_design.py`）：呼吸灯 PWM 调光，走通完整闭环。

### 修复

- `-DDUMP_VCD` 死开关：testbench 模板补上 `#ifdef DUMP_VCD` 波形转储块，`--dump` 真正生效。

## [0.1.0] - 2026-09-01

首个 MVP：从自然语言需求 / 结构化 Spec 到可交付 HDL 设计包的
「AI 辅助数字硬件设计闭环」。当前支持一种受限设计类别 —— 流水灯（LED chaser）。

### 新增

- 需求澄清层（`hdc/clarify.py`）：确定性关键词提取 + 默认值兜底，报告「识别/假设/提示」。
- Spec JSON 定义与校验（`hdc/spec.py`）：唯一事实来源，含派生参数（divider、tick_width 等）。
- 参数化 RTL 模板与自检 testbench 模板（`templates/`，`{{ key }}` 占位符渲染）。
- 模板生成器（`hdc/generate.py`）：Spec → 可综合 RTL / 属性断言 testbench。
- 工具链检测（`hdc/toolchain.py`）：定位 iverilog/vvp/yosys，支持 OSS CAD Suite 便携版。
- 自动仿真 + 综合 + 结果分类（`hdc/verify.py`）。
- 错误注入（`hdc/inject.py`）：4 类缺陷，用于证明 testbench 能检出。
- 模块框图 / 状态转移图（`hdc/diagram.py`，SVG，随 Spec 参数生成）。
- 闭环编排与打包（`hdc/pipeline.py`）+ 命令行入口（`hdc/__main__.py`）。
- 端到端演示（`hdc/demo.py`，`python -m hdc --demo`）。
- 项目 skill（`.claude/skills/hdc-loop/`），沉淀标准工作流。

### 修复

- 相对 `--out` 路径下 vvp 打开 `sim.vvp` 错位（统一 `resolve()` 为绝对路径）。
- yosys 便携版 DLL 依赖：子进程环境加入 `lib/`。
- `wrong_interval` 注入被 testbench 参数覆盖：改为修改内部移位阈值。

# Changelog

本项目的显著变更都会记录在此。格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

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

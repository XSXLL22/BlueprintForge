# Changelog

本项目的显著变更都会记录在此。格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.0] - 2026-09-02

第二段流水线落地：从一份 Verilog 到**嘉立创可上传的制造文件**，全程命令行，没有一次
鼠标点击。路线是离散 74HC 逻辑门 + KiCad。4 位计数器端到端 `结论 : OK`（DRC 0 违规）。

**尚未真板验证** —— 所有结论来自 KiCad 的 DRC 与对 Gerber 的格式自查。

### 新增

- 74 系列综合（`hdc/pcb/synth74.py`）：yosys + vendored `74xx-liberty` →
  门级网表 JSON；yosys 脚本落盘可复现。
- 芯片知识库（`hdc/pcb/cells.py`）：手写脚位 / 功能名 / 电气类型，按数据手册单测对照；
  未收录的 cell 抛 `UnmappedCellError` 并给出补救提示，不静默跳过。
- 装箱（`hdc/pcb/pack.py`）：cell → DIP 芯片 + 引脚号，宽单元优先、同片复用槽位。
- 板级外围（`hdc/pcb/peripheral.py`）：电源排针 + 体电容 + 每片去耦 + 74HC14 RC 时钟
  （带时钟源跳线）+ 按键复位 + 每输出位 LED 限流 + 输入排针下拉；参数集中在
  `BoardOptions`。
- 原理图（`hdc/pcb/schematic.py`）：纯函数渲染 `.kicad_sch`，自制 DIP 符号 + 标签连线，
  图框注释自动写入每段外围的接法。
- 封装（`hdc/pcb/footprints.py`）：读已装 KiCad 的官方库，几何原样内嵌进板文件。
- 布局与铺铜（`hdc/pcb/layout.py`）：货架式摆件 + 自动板框 + B.Cu 整层 GND；
  铺铜填充调 KiCad 自带 Python 的 `pcbnew`。
- A\* 迷宫布线（`hdc/pcb/router.py`）：焊盘膨胀成禁区，按层计代价，顶层堵死才打过孔。
- 制造文件（`hdc/pcb/manufacture.py`）：DRC + 7 层 Gerber + 钻孔 + BOM + CPL + 两份 PDF
  + 嘉立创格式转换 + 可复现 ZIP（时间戳写死 1980-01-01）。
- 链路编排与送厂判据（`hdc/pcb/pipeline.py`）：`PcbResult.ok` 五条同时成立才为真，
  `errors()` 逐条列出拦路问题。
- `--pcb` 开关与直接给 `.v` 的入口（`hdc/__main__.py`）。
- 基准示例独立成目录（`examples/counter/`）：RTL + 自检 TB + 说明，作为单测的事实来源
  （经 `tests/examples.py` 读取，`tests/test_examples.py` 守住契约）。
- 文档：重写 `README.md`（技术路线 + 12 条实现要点 + 已实现/未实现如实分级）、
  `USAGE.md` 扩成三部分技术手册（新增电路板层 7 节）、新增 `docs/architecture.md`。

### 修复

- Windows 上把输出重定向到管道/文件时，摘要里的 `mm²` 触发
  `UnicodeEncodeError: 'gbk' codec` —— 电路板已经做完了才崩在打印上。新增
  `hdc/console.py`，启动时把 `sys.stdout` / `sys.stderr` 钉成 UTF-8。

### 变更

- 计数器电路从测试代码里的字符串常量搬进 `examples/counter/*.v`：`hdc/` 与 `tests/`
  中不再出现任何具体电路，测试验的就是用户手上那一份文件。
- 回归规模 272 → **287 项**（`python -m unittest discover tests`，约 156 s）。

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

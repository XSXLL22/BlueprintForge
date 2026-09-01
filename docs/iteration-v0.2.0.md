# 迭代 v0.2.0 — GitHub 提交更新说明

> 本文按 GitHub 提交/评审的格式，整理本次迭代（`af5e1ec..56d0bb7`，共 12 个提交）
> 的完整变更，并逐条附上**必要注解**（为什么这么做、技术要点、影响面）。
> 对应版本 CHANGELOG 见 [`../CHANGELOG.md`](../CHANGELOG.md)。

---

## 一、概述

本次迭代的主题是**架构升级**：把 hdc 的生成侧从「模板填充」升级为
**「LLM 自由生成 + 工具链验证」**。核心动机——机械化案例整理覆盖不了真实需求，
必须依托大模型做逻辑设计；设计边界不再人为设限，取决于模型的电子电路知识与
逻辑设计能力。

落地为两条路径：

- **开发期**：用 Claude 作为设计大脑（`.claude/skills/hdc-design` 工作流 + 演示脚本）。
- **交付期**：用户接入自己的 API（Anthropic / OpenAI 兼容 / Ollama），
  `python -m hdc --design "需求"` 一条命令端到端自动设计 + 验证 + 修复。

验证侧只约定 **4 条最小硬契约**，其余完全自由：

1. RTL 顶层模块名 = `project`；TB 顶层模块名 = `tb_<project>`。
2. TB 结束时打印精确字符串 `SIM_RESULT: PASS` / `SIM_RESULT: FAIL`，`$finish` 在其后。
3. RTL 可综合（Verilog-2001，无 `initial`）。
4. 建议（非强制）：逐条断言打印 `CHECK <name>: PASS/FAIL`。

---

## 二、提交清单与注解

### 阶段 A —— 开发期用 Claude 做设计大脑

#### `fix(verify): 修复 -DDUMP_VCD 死开关，波形转储生效`（`5102ed5`）

- **文件**：`templates/tb_led_chaser.v.tpl`
- **注解**：`--dump` 一直向 iverilog 传 `-DDUMP_VCD`，但 testbench 模板里没有对应的
  `#ifdef DUMP_VCD` 块，波形**永远不生成**（死开关）。补上
  `$dumpfile("waveform.vcd")` + `$dumpvars` 后，`--dump` 才真正生效。

#### `feat(design): 新增 LLM 自由设计产物契约与验证编排`（`7edcbdd`）

- **文件**：`hdc/design.py`（新增）
- **注解**：新架构的地基。`Design` dataclass 是一份 LLM 设计的完整产物
  （project/requirement/rtl/tb/design_json/state_machine_md/concept_md）；
  `write_artifacts` / `load_artifacts` 落盘与读回；`verify_design` 复用
  `hdc.verify` 的仿真/综合做单轮验证，返回 `VerifyOutcome`（含 `errors()` 错误分类）。
  **关键决策**：契约刻意只约定上面 4 条硬约束，其余端口/参数/结构完全自由——
  否则 LLM 会被流水灯的 `LED_COUNT`/`RESET_LED` 等专用锚点锁死。

#### `feat(diagram): 泛化图纸生成，从 design.json 支持任意设计`（`7afbbc4`）

- **文件**：`hdc/diagram.py`
- **注解**：新增 `generate_generic_block_diagram` / `generate_generic_state_diagram` /
  `write_design_diagrams`，从 `design.json` 的 `interface` + 可选 `state_machine`
  生成通用模块框图与状态转移图。原流水灯专用函数保留并标记 legacy，不破坏旧路径。

#### `feat(skill): 新增 hdc-design 设计工作流 skill`（`5bd134d`）

- **文件**：`.claude/skills/hdc-design/SKILL.md`
- **注解**：把「开发期用 Claude 做逻辑设计」沉淀为标准流程：读需求 → 设计接口/状态机 →
  写 design.json + RTL + 自检 TB + 构想 → `verify_design` 验证 → 按错误分类定向修复。
  这是阶段 B 的 `design_with_fix` 在「人（Claude）」这一侧的等价物。

#### `test(design): 产物往返 + 最小硬契约自检`（`872e252`）

- **文件**：`tests/test_design.py`
- **注解**：5 项测试。往返（写盘→读回字段一致）；`verify_design` 对合法计数器 PASS；
  对「TB 顶层名错误」判 `compile_error`；对「缺 SIM_RESULT 标记」判 `assertion_failure`。
  用**最小计数器**（完全脱离流水灯模板）证明契约的通用性。

#### `demo(design): 呼吸灯 PWM 调光演示，走通 LLM 自由设计闭环`（`c2467c7`）

- **文件**：`examples/demo_llm_design.py`
- **注解**：现场设计一个流水灯模板**完全覆盖不了**的案例（三角波亮度 + PWM 调光），
  仿真 5 项断言全 PASS + yosys 综合无 error + 图纸生成，证明新架构成立。

### 阶段 B —— 交付期用户接入自己的 API

#### `feat(llm): 新增 LLM provider 抽象与契约注入`（`03d5edf`）

- **文件**：`hdc/llm.py`（新增）
- **注解**：纯标准库（`urllib.request`，零第三方依赖）实现三种 provider：
  `AnthropicProvider`（Messages API）、`OpenAIProvider`（OpenAI 兼容
  Chat Completions，可接 DeepSeek/Kimi/通义等改 `base_url`）、`OllamaProvider`
  （本地 `/api/chat`）。配置读 `HDC_*` 环境变量或 `~/.hdc/config`。
  `SYSTEM_PROMPT` 把 4 条硬契约 + JSON 输出格式注入 system prompt；
  `generate_design` 解析模型产出为 `Design`；`design_with_fix` 做**有界修复**
  （把错误分类反馈给模型重写，默认 3 轮）。

#### `feat(cli): 新增 --design 命令，LLM 自动设计闭环`（`412ab22`）

- **文件**：`hdc/__main__.py`
- **注解**：交付期入口 `python -m hdc --design "需求"`。友好处理配置缺失与调用失败
  （退出码 `2`），设计失败返回 `1`。

#### `test(llm): provider 抽象与修复闭环单测（纯 mock + 本地 HTTP）`（`4bb687d`）

- **文件**：`tests/test_llm.py`
- **注解**：11 项测试。配置读取、JSON 解析（含 ` ```json ` 代码块剥离）、修复闭环
  收敛/达上限放弃；以及用 `http.server` 起本地 OpenAI 兼容端点做**真实 urllib 端到端**
  ——证明「用户接 API → 自动设计 → 验证」不是 mock 出来的。

### 文档与工具测试

#### `docs: 更新 README/USAGE 反映 LLM 自由设计路径`（`b886d1f`）

- **文件**：`README.md`、`USAGE.md`
- **注解**：说明两条生成路径、核心原则（LLM 是设计大脑、工具链是安全网）、
  `--design` 用法、三种 provider 配置与 4 条硬契约。

#### `docs: CHANGELOG 记录 v0.2.0 LLM 自由设计架构升级`（`fcc1ea6`）

- **文件**：`CHANGELOG.md`

#### `demo(design): 新增冒泡排序工具测试，验证 LLM 自由设计任意电路`（`56d0bb7`）

- **文件**：`examples/demo_bubble_sort.py`
- **注解**：本次**工具测试**。模拟「接入的 LLM API」现场设计一个**冒泡排序逻辑电路**
  （输入 0~8 位 4-bit 数据，不足 8 位以 0 = 灯灭占位），走 `verify_design` 闭环：
  4 组测试向量（占位符 / 全零 / 逆序满 / 含重复）×（升序 + 多重集一致）共 9 项断言
  全 PASS，yosys 综合无 error。证明新架构能覆盖排序器这类与流水灯无关的电路。

---

## 三、验证清单

| 验证项 | 结果 |
|--------|------|
| `python -m unittest discover tests` | **55 项全过**（原 39 + 新增 16） |
| 呼吸灯演示（`examples/demo_llm_design.py`） | 仿真 PASS + 综合 OK |
| 冒泡排序工具测试（`examples/demo_bubble_sort.py`） | 仿真 9 断言 PASS + 综合 OK |
| `--design` 配置/网络错误路径 | 友好提示 + 退出码 2 |
| LLM 修复闭环（mock + 本地 HTTP） | 收敛正确、达上限正确放弃 |

---

## 四、兼容性与影响面

- **向后兼容**：模板路径（`python -m hdc specs/...`）与 `--demo` 完全保留，
  旧 Spec/生成/注入/流水灯图纸逻辑未改动。
- **新增依赖**：无。`hdc/llm.py` 纯标准库，不引入第三方包。
- **破坏性变更**：无。新增的是 `hdc/design.py`、`hdc/llm.py`、`--design` 命令与
  通用图纸函数，旧接口签名不变。
- **已知限制**：LLM 路径依赖模型自身能力，需用户配置 API key；占位符约定
  （灯灭 = 0）在排序等场景下 0 会被视为最小值排在前面，属于需求语义而非缺陷。

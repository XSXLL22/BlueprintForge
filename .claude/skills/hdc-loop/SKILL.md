---
name: hdc-loop
description: hdc 数字硬件设计闭环的标准执行流程。当需要根据 Spec JSON 生成流水灯 RTL/testbench、跑 iverilog 仿真与 yosys 综合、打包输出，或修改 hdc 工具链本身时使用。
---

# hdc 数字硬件设计闭环（标准执行流程）

从自然语言需求到可交付 HDL 设计包的收敛闭环。当前 MVP 只支持**流水灯（LED chaser）**
一类设计，工具链为 `iverilog`（仿真）+ `yosys`（综合），纯 Python 标准库生成。

## 何时使用

- 用户要新增/修改一个流水灯设计（改参数、改方向、改使能、改复位）
- 用户要跑闭环：生成 → 仿真 → 综合 → 打包
- 用户要验证 testbench 能检出错误（错误注入）
- 用户要改动 `hdc/` 工具链本身（模板、生成器、验证脚本）

## 核心不变量（改动时必须遵守）

1. **Spec JSON 是唯一事实来源**：所有下游只读 `Spec`，不读用户字符串。
2. **模板为主**：RTL/testbench 用 `templates/*.tpl` 的 `{{ key }}` 占位符渲染，生成器只填
   参数；不要手写自由 HDL 塞进生成结果。
3. **验证基于属性断言**：testbench 逐项断言复位态/使能保持/方向/间隔/wrap/无 X-Z，
   任何一项失败即 `SIM_RESULT: FAIL`。
4. **可综合**：Yosys 综合无 error 才通过；警惕锁存器推断（`latches_inferred`）。
5. **错误分类 + 有界修复**：错误分类后最多修复 `max_fix_rounds` 轮，超限回退保守方案。
6. **纯标准库**：`hdc/` 不引入第三方 Python 依赖。
7. **每次优化都提交**：一条变更一个 `git commit`，message 用 Conventional Commits
   （`feat:`/`fix:`/`docs:`/`test:`/`refactor:`），中文描述。

## 标准流程

### 1. 改需求 → 改 Spec JSON

编辑 `specs/<project>.json`。字段速查（默认值见 `hdc/spec.py` 的 `DEFAULTS`）：

| 字段 | 说明 | 允许值 |
|------|------|--------|
| `clock.freq_mhz` | 时钟频率 | > 0 |
| `clock.reset` | 复位类型 | `async_active_low` / `async_active_high` |
| `behavior.led_count` | LED 数 | ≥ 2 |
| `behavior.direction` | 方向 | `left_to_right` / `right_to_left` |
| `behavior.interval_ms` | 切换间隔 | > 0 |
| `behavior.wrap` | 是否循环 | bool |
| `behavior.enable_port` | 是否带使能 | bool |
| `behavior.enable_polarity` | 使能极性 | `active_high` / `active_low` |

派生参数（勿手填）：`divider = freq_mhz × 1000 × interval_ms`；`tick_width` 由 divider 反推。

### 2. 跑闭环

```bash
# 快速配置（interval=1ms，秒级）：日常验证用这个
python -m hdc specs/led_chaser_fast.json

# 默认 500ms：仿真约 1.25 亿周期，几十秒到几分钟
python -m hdc specs/led_chaser.json

# 指定输出目录 / 导出 VCD 波形 / 注入错误（预期「仿真失败」）
python -m hdc specs/led_chaser_fast.json --out output
python -m hdc specs/led_chaser_fast.json --dump
python -m hdc specs/led_chaser_fast.json --inject wrong_direction
```

产物落在 `output/<project>/`：`rtl/`、`tb/`、`sim/`、`synth/`、`diagrams/`、`docs/`。
判定标准：终端输出 `结果: OK`；`sim/sim.log` 末尾有 `SIM_RESULT: PASS`；`synth/synth.log`
无 `ERROR`。

### 3. 回归测试

```bash
# 不依赖模拟器的单测（Spec / 生成 / 注入 / 图纸）
python -m unittest tests.test_spec tests.test_generate tests.test_inject tests.test_diagram

# 全部（含端到端 + 错误注入 + 相对路径回归；未装工具链自动跳过）
python -m unittest discover tests
```

### 4. 提交

```bash
git add -A && git commit -m "<type>(<scope>): <中文描述>"
```

## 改动工具链时的检查点

- 改了 `templates/*.tpl` 的占位符 → 同步 `generate.py` 的 `rtl_values`/`tb_values`。
- 改了 `inject.py` 的 bug → 确认 testbench 仍能检出（`--inject <bug>` 必须失败）。
- 涉及子进程路径 → 统一 `Path(...).resolve()`（相对 `--out` 时 vvp 的 cwd 会错位）。
- 涉及 DLL 依赖（yosys）→ 用 `toolchain.env_for()` 把 `bin/` 与 `lib/` 加进 PATH。

## 文件速查

- `hdc/spec.py` — Spec 加载/校验/派生参数
- `hdc/generate.py` — 模板渲染（Spec → RTL/tb）
- `hdc/verify.py` — 仿真/综合 + 结果解析
- `hdc/diagram.py` — 模块框图/状态转移图（SVG）
- `hdc/inject.py` — 错误注入（验收）
- `hdc/pipeline.py` — 闭环编排 + 打包
- `hdc/__main__.py` — CLI 入口
- `specs/led_chaser_fast.json` — 快速测试配置（回归用）

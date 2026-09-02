# 贡献指南

感谢你考虑为 hdc 做贡献。本项目把一句话需求做成可打样的电路板，分两段：逻辑层
（`hdc/`）产 RTL，电路板层（`hdc/pcb/`）把 RTL 做成嘉立创可上传的文件。
逻辑层有两条生成路径 —— **模板路径当前只覆盖流水灯**，**LLM 自由设计路径不限电路种类**。
模块划分与接口边界见 [docs/architecture.md](./docs/architecture.md)。

## 开发环境

| 要动哪一段 | 需要装什么 | 不装会怎样 |
|-----------|-----------|-----------|
| 全部 | Python 3.9+（`hdc/` 只用标准库，无第三方依赖） | — |
| 仿真 | [Icarus Verilog](http://iverilog.icarus.com/)（`iverilog` + `vvp`） | 相关测试自动跳过 |
| 综合 / 74 系列综合 | [Yosys](https://yosyshq.net/yosys/) | 同上 |
| 板图 / 制造文件 | [KiCad](https://www.kicad.org/) ≥8（实测 10.0.6） | 同上 |
| LLM 自由设计 | 一个 LLM API（或本地 Ollama） | `--design` 报配置错误 |

Windows 安装：`choco install iverilog -y`；yosys 用
[OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build/releases) 便携版，
解压后设环境变量 `OSS_CAD_SUITE` 指向套件根目录；KiCad 用官方安装包或
`winget install KiCad.KiCad`。逐平台命令见 [USAGE.md 第 2、11 节](./USAGE.md#2-环境准备)。

未安装工具链也能贡献：不依赖外部工具的单测会照常跑，需要工具的用例**自动跳过而不是
假通过**。

## 运行测试

测试按「要装什么」分三层，装饰器写明依赖：

| 层 | 装饰器 | 覆盖 |
|----|-------|------|
| 不需要外部工具 | 无 | Spec、生成、注入、图纸、芯片知识库、装箱、外围、格式转换、判据逻辑、输出编码、示例契约 |
| 只需要 yosys | `@skipUnless(TC.can_synthesize)` | 74 系列综合、只出原理图的链路 |
| yosys + KiCad | `@skipUnless(... and kicad.find_cli())` | 摆件、布线、铺铜、DRC、Gerber 导出、全链路 |

```bash
# 秒级：不依赖任何外部工具的部分
python -m unittest tests.test_spec tests.test_generate tests.test_inject \
    tests.test_console tests.test_examples tests.test_pcb_cells

# 全部（287 项，约 156 s；缺工具的用例自动跳过）
python -m unittest discover tests
```

提交前请确保全部测试通过。

## 代码约定

- 纯标准库，不引入第三方运行时依赖。
- 新代码配对应单测（`tests/test_*.py`）。**判定尽量不看实现**：产物查文件系统，元件清单
  读导出的 CSV，坐标系拿 `.drl` 的孔位反过来校验 `cpl.csv`。
- **具体电路只放在 `examples/`**。`hdc/` 与 `tests/` 里不出现任何电路文本；测试经
  `tests/examples.py` 读 `examples/counter/*.v`，所以验的就是用户手上那一份。改了示例要
  跑全量测试（`tests/test_examples.py` 守着它的契约）。
- 涉及子进程文件路径时统一 `Path(...).resolve()`（相对 `--out` 会触发 vvp cwd 错位）。
- 涉及 yosys 等便携工具链的 DLL 依赖时，用 `toolchain.env_for()` 构造子进程环境。
- 打印非 ASCII 字符前不必特别处理：`hdc/console.py` 在启动时已把标准输出钉成 UTF-8。
- 动电路板层之前**先读 [pcb-roadmap.md 的「实施偏差」](./docs/pcb-roadmap.md#实施偏差)** ——
  里面几条（CPL 的 Y 不取反、铺铜层走线加代价、封装原样内嵌）看着反直觉，改回去会坏。

## 提交规范

一条变更一个 commit，message 用 Conventional Commits：

```
<type>(<scope>): <中文描述>
```

- `feat:` 新功能，`fix:` 修复，`docs:` 文档，`test:` 测试，`refactor:` 重构
- scope 用模块名，如 `feat(clarify): ...`、`fix(verify): ...`、`feat(pcb): ...`

## 扩展方向

按「改哪个模块」列，接口边界见
[docs/architecture.md 第四节](./docs/architecture.md#四接口边界哪里可以替换)：

- **补芯片**（最常见）：综合出库里没有的 cell 会抛 `UnmappedCellError`。在
  `hdc/pcb/cells.py` 的 `CELLS` 表按数据手册加一条（型号、封装、电源脚、片内槽位），
  并补对应单测逐条对照手册。
- **改外围电路**：`hdc/pcb/peripheral.py` 的 `BoardOptions` 是数据类 —— 改字段就能改
  时钟频率、LED 亮度、上拉阻值，不动逻辑。加新外围段落则在 `_add_*` 里加函数。
- **换布线器**（T2.1）：`router.route()` 是 A\* 迷宫。换 FreeRouting 需要自研
  `.kicad_pcb → .dsn` 转换与 SES 回灌 —— `kicad-cli` 不支持导出 `.dsn`。
- **接真实 LLM 澄清层**：把 `hdc/clarify.py` 里 `clarify()` 的规则替换为 LLM 结构化
  抽取，返回 `Clarification` 接口不变，下游无感。
- **新设计类别（模板路径）**：目前 `type` 只作文档用途。要支持计数器 / PWM 等，新增对应
  `templates/*.tpl` + `generate.py` 分支，复用 `verify.py` / `pipeline.py`。
  （LLM 自由设计路径本来就不受类别限制，不必为此改模板。）
- **更多复位类型 / 同步复位**：扩展 `hdc/spec.py` 的 `RESET_TYPES`，同步模板与 testbench。
- **通用市售芯片选型**：项目的下一阶段目标 —— 让模型不只用 74HC，而是从市面在售器件
  （含 MCU、专用芯片）里选型。这需要一份可查询的器件库，`cells.py` 是它的雏形。

## 报告问题

用 [issue 模板](./.github/ISSUE_TEMPLATE.md) 描述复现步骤、期望与实际行为、环境
（OS / Python 版本 / 工具链版本）。安全相关问题请勿公开披露，直接联系维护者。

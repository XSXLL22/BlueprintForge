# 贡献指南

感谢你考虑为 hdc 做贡献。本项目是「AI 辅助数字硬件设计闭环」的 MVP，当前只支持
流水灯一种设计类别，并有意保持最小、可扩展的形态。

## 开发环境

- Python 3.9+（`hdc/` 只用标准库，无第三方依赖）
- [Icarus Verilog](http://iverilog.icarus.com/)（`iverilog` + `vvp`）—— 仿真
- [Yosys](https://yosyshq.net/yosys/)（`yosys`）—— 综合

Windows 安装：`choco install iverilog -y`；yosys 用
[OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build/releases) 便携版，
解压后设环境变量 `OSS_CAD_SUITE` 指向套件根目录。

未安装工具链也能贡献：不依赖模拟器的单测（Spec / 生成 / 注入 / 图纸）会照常跑，
端到端测试会自动跳过。

## 运行测试

```bash
# 纯 Python 单测（不依赖工具链）
python -m unittest tests.test_spec tests.test_generate tests.test_inject \
    tests.test_diagram tests.test_clarify

# 全部（含端到端 + 错误注入；未装工具链时自动跳过）
python -m unittest discover tests
```

提交前请确保全部测试通过。

## 代码约定

- 纯标准库，不引入第三方运行时依赖。
- 新代码配对应单测（`tests/test_*.py`）。
- 涉及子进程文件路径时统一 `Path(...).resolve()`（相对 `--out` 会触发 vvp cwd 错位）。
- 涉及 yosys 等便携工具链的 DLL 依赖时，用 `toolchain.env_for()` 构造子进程环境。

## 提交规范

一条变更一个 commit，message 用 Conventional Commits：

```
<type>(<scope>): <中文描述>
```

- `feat:` 新功能，`fix:` 修复，`docs:` 文档，`test:` 测试，`refactor:` 重构
- scope 用模块名，如 `feat(clarify): ...`、`fix(verify): ...`

## 扩展方向

- **接真实 LLM 澄清层**：把 `hdc/clarify.py` 里 `clarify()` 的规则替换为 LLM 结构化
  抽取，返回 `Clarification` 接口不变，下游无感。
- **新设计类别**：目前 `type` 只作文档用途。要支持计数器 / PWM 等，建议新增对应的
  `templates/*.tpl` + `generate.py` 分支，并复用 `verify.py` / `pipeline.py`。
- **更多复位类型 / 同步复位**：扩展 `hdc/spec.py` 的 `RESET_TYPES`，同步模板与 testbench。

## 报告问题

用 [issue 模板](./.github/ISSUE_TEMPLATE.md) 描述复现步骤、期望与实际行为、环境
（OS / Python 版本 / 工具链版本）。安全相关问题请勿公开披露，直接联系维护者。

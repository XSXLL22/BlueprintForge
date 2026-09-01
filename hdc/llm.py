"""LLM provider 抽象：交付期让用户接入自己的 API 做逻辑设计推理。

纯标准库（``urllib.request``），零第三方运行时依赖。支持三种 provider：

- ``anthropic`` — Messages API（``/v1/messages``）
- ``openai``    — OpenAI 兼容 Chat Completions（``/v1/chat/completions``，覆盖多数兼容服务）
- ``ollama``    — 本地 Ollama（``/api/chat``）

配置优先级：显式参数 > 环境变量 > 配置文件（``~/.hdc/config``）。
环境变量：``HDC_PROVIDER`` / ``HDC_API_KEY`` / ``HDC_MODEL`` / ``HDC_BASE_URL``。

核心闭环：``generate_design`` 让模型产出完整 :class:`~hdc.design.Design`，
``design_with_fix`` 用 ``verify_design`` 验证并做**有界修复循环**（把错误分类
反馈给模型重写，最多 ``max_fix_rounds`` 轮）。这就是「AI 接收需求 → 自动设计 →
工具链验证 → 自动修复」的全过程。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from hdc.design import Design, VerifyOutcome, verify_design

# ---- 契约注入 -----------------------------------------------------------------

SYSTEM_PROMPT = """你是数字电路逻辑设计专家。根据用户需求，设计一个可综合的 Verilog 模块，
并写出自检 testbench、结构化描述、状态机与设计构想。

只输出一个 JSON 对象（不要任何多余文字，不要 Markdown 代码块），字段如下：

{
  "project": "模块名（小写字母/数字/下划线，作为 Verilog 顶层模块名）",
  "requirement": "需求原文",
  "rtl": "可综合 Verilog 源码（顶层模块名必须等于 project）",
  "tb": "自检 testbench 源码（顶层模块名必须等于 tb_<project>）",
  "design_json": {
    "project": "同上",
    "requirement": "同上",
    "interface": {"inputs": ["..."], "outputs": ["..."]},
    "state_machine": {"reset_state": "...", "states": ["..."], "transitions": [{"from": "...", "to": "...", "label": "..."}]},
    "concept": "一句话设计构想"
  },
  "state_machine_md": "状态机描述（含 mermaid 代码块，若无需状态机可省略）",
  "concept_md": "设计构想：思路/权衡/约束（Markdown）"
}

必须遵守的硬约定（否则会被工具链判失败）：
1. RTL 顶层模块名 = project；testbench 顶层模块名 = tb_<project>。
2. testbench 结束时打印精确字符串 "SIM_RESULT: PASS"（通过）或 "SIM_RESULT: FAIL"，
   且 $finish 在其之后。逐条断言用 $display("CHECK <name>: PASS/FAIL")。
3. RTL 可综合：Verilog-2001，无 initial 块，寄存器用非阻塞赋值 <=。
4. 波形（可选）用 `ifdef DUMP_VCD 包裹 $dumpfile/$dumpvars。

端口名、参数名、内部结构、状态机组织完全自由，按需求本身来设计。
"""


class LLMError(Exception):
    """LLM 调用失败（网络 / 鉴权 / 协议解析）。"""


class LLMProvider(Protocol):
    def chat(self, system: str, user: str) -> str:  # 返回模型文本输出
        ...


@dataclass
class _ProviderSpec:
    key: str
    model: str
    base_url: str


_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.1",
}
_DEFAULT_BASE = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "ollama": "http://localhost:11434",
}


# ---- 配置读取 ----------------------------------------------------------------

def _config_path() -> Path:
    return Path("~/.hdc/config").expanduser()


def load_config() -> dict:
    """合并配置文件 + 环境变量（环境变量优先，覆盖非空项）。"""
    cfg: dict = {}
    path = _config_path()
    if path.is_file():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    for key in ("provider", "api_key", "model", "base_url"):
        env = os.environ.get(f"HDC_{key.upper()}")
        if env:
            cfg[key] = env
    return cfg


# ---- HTTP 基础 ---------------------------------------------------------------

def _post_json(url: str, body: dict, headers: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**headers, "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise LLMError(f"HTTP {e.code}：{detail[:400]}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"网络错误：{e.reason}") from e


# ---- 各 provider -------------------------------------------------------------

class AnthropicProvider:
    def __init__(self, api_key: str, model: str = "claude-sonnet-5",
                 base_url: str = "https://api.anthropic.com"):
        self.api_key, self.model, self.base_url = api_key, model, base_url

    def chat(self, system: str, user: str) -> str:
        resp = _post_json(
            self.base_url.rstrip("/") + "/v1/messages",
            {"model": self.model, "max_tokens": 8192, "system": system,
             "messages": [{"role": "user", "content": user}]},
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )
        try:
            return resp["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Anthropic 响应解析失败：{resp}") from e


class OpenAIProvider:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 base_url: str = "https://api.openai.com"):
        self.api_key, self.model, self.base_url = api_key, model, base_url

    def chat(self, system: str, user: str) -> str:
        resp = _post_json(
            self.base_url.rstrip("/") + "/v1/chat/completions",
            {"model": self.model, "temperature": 0.2,
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": user}]},
            {"authorization": f"Bearer {self.api_key}"},
        )
        try:
            return resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"OpenAI 响应解析失败：{resp}") from e


class OllamaProvider:
    def __init__(self, model: str = "llama3.1",
                 base_url: str = "http://localhost:11434"):
        self.model, self.base_url = model, base_url

    def chat(self, system: str, user: str) -> str:
        resp = _post_json(
            self.base_url.rstrip("/") + "/api/chat",
            {"model": self.model, "stream": False,
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": user}]},
            {},
        )
        try:
            return resp["message"]["content"]
        except (KeyError, TypeError) as e:
            raise LLMError(f"Ollama 响应解析失败：{resp}") from e


def provider_from_config(cfg: dict | None = None) -> LLMProvider:
    cfg = cfg if cfg is not None else load_config()
    provider = (cfg.get("provider") or "openai").lower()
    api_key = cfg.get("api_key") or ""
    model = cfg.get("model") or _DEFAULT_MODELS.get(provider, "")
    base_url = cfg.get("base_url") or _DEFAULT_BASE.get(provider, "")

    if provider == "anthropic":
        if not api_key:
            raise LLMError("缺少 API key：设置 HDC_API_KEY 或 ~/.hdc/config 的 api_key")
        return AnthropicProvider(api_key, model, base_url)
    if provider == "ollama":
        return OllamaProvider(model, base_url)
    if provider == "openai":
        if not api_key:
            raise LLMError("缺少 API key：设置 HDC_API_KEY 或 ~/.hdc/config 的 api_key")
        return OpenAIProvider(api_key, model, base_url)
    raise LLMError(f"未知 provider：{provider}（支持 anthropic / openai / ollama）")


# ---- 生成与修复闭环 -----------------------------------------------------------

def _extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON 对象（容忍 ```json 代码块与前后说明）。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError(f"模型输出中没有 JSON 对象：{text[:400]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"模型输出 JSON 解析失败：{e}") from e


def parse_design(text: str) -> Design:
    """把模型返回的 JSON 文本解析为 :class:`Design`。"""
    data = _extract_json(text)
    project = str(data.get("project", "")).strip()
    rtl = data.get("rtl", "")
    tb = data.get("tb", "")
    if not project or not rtl or not tb:
        raise LLMError("模型输出缺少 project / rtl / tb 字段")

    dj = dict(data.get("design_json") or {})
    dj.setdefault("project", project)
    dj.setdefault("requirement", data.get("requirement", ""))
    dj.setdefault("interface", {"inputs": [], "outputs": []})

    return Design(
        project=project,
        requirement=data.get("requirement", ""),
        rtl=rtl,
        tb=tb,
        design_json=dj,
        state_machine_md=data.get("state_machine_md", ""),
        concept_md=data.get("concept_md", ""),
    )


def generate_design(
    provider: LLMProvider,
    requirement: str,
    *,
    fixed_project: str | None = None,
    feedback: str | None = None,
) -> Design:
    """让模型根据需求产出一份设计；修复轮可固定 project 并附带错误反馈。"""
    system = SYSTEM_PROMPT
    if fixed_project:
        system += (
            f"\n\n【本轮硬性要求】project 字段必须固定为 `{fixed_project}`，"
            "不要改名，RTL/TB 顶层名也相应固定。"
        )
    user = f"需求：{requirement}"
    if feedback:
        user += (
            f"\n\n你上一轮设计未通过工具链验证，错误如下，请修复后重新输出完整 JSON：\n{feedback}"
        )
    return parse_design(provider.chat(system, user))


def design_with_fix(
    provider: LLMProvider,
    requirement: str,
    out_dir: Path,
    *,
    max_fix_rounds: int = 3,
    run_synth: bool = True,
    dump_vcd: bool = False,
) -> VerifyOutcome:
    """「生成 → 仿真+综合 → 错误分类 → 反馈重写」的有界闭环。"""
    design = generate_design(provider, requirement)
    out = verify_design(design, out_dir, run_synth=run_synth, dump_vcd=dump_vcd)

    for _ in range(1, max_fix_rounds):
        if out.ok:
            break
        feedback = "\n".join(out.errors())
        design = generate_design(
            provider, requirement, fixed_project=design.project, feedback=feedback,
        )
        out = verify_design(design, out_dir, run_synth=run_synth, dump_vcd=dump_vcd)
    return out

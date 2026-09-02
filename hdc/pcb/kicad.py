"""定位并调用 KiCad 命令行工具。

PCB 后半段（原理图导出、布局、Gerber）都要用 KiCad，但它的安装位置在各平台上
差别很大，而且 `pcbnew` 模块只在 **KiCad 自带的 Python** 里能 import（系统 Python
通常版本不匹配）。这里把这两件事收成一个小接口：

- `find_cli()` / `find_python()`：按「环境变量 → PATH → 常见安装目录」的顺序找。
- `require_cli()`：找不到时抛出带安装提示的错误，而不是让下游报 FileNotFoundError。
- `run()` / `run_python()`：统一 UTF-8 解码与超时，返回 `CompletedProcess`。

用环境变量 `HDC_KICAD_CLI` / `HDC_KICAD_PYTHON` 可以指定任意路径，便于 CI。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

#: 覆盖自动查找的环境变量名。
CLI_ENV = "HDC_KICAD_CLI"
PYTHON_ENV = "HDC_KICAD_PYTHON"

#: 命令默认超时（秒）。Gerber 导出在慢机器上也够。
TIMEOUT = 300

#: KiCad 主版本目录名（新版本在前，找到即用）。
_VERSIONS = ("10.0", "9.0", "8.0", "7.0")


class KicadError(RuntimeError):
    """KiCad 缺失或命令执行失败。"""


def _windows_roots(env: Mapping[str, str]) -> list[Path]:
    keys = ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")
    bases = [Path(env[k]) / "Programs" / "KiCad" for k in keys if env.get(k)]
    bases += [Path(env[k]) / "KiCad" for k in keys if env.get(k)]
    return [b / v / "bin" for b in bases for v in _VERSIONS]


def _posix_roots(env: Mapping[str, str]) -> list[Path]:
    roots = [Path("/usr/bin"), Path("/usr/local/bin"),
             Path("/Applications/KiCad/KiCad.app/Contents/MacOS")]
    if env.get("HOME"):
        roots.append(Path(env["HOME"]) / ".local" / "bin")
    return roots


def _search_dirs(env: Mapping[str, str]) -> list[Path]:
    return _windows_roots(env) if os.name == "nt" else _posix_roots(env)


def _locate(name: str, env_var: str, env: Mapping[str, str] | None) -> Path | None:
    env = os.environ if env is None else env
    override = env.get(env_var)
    if override:
        path = Path(override)
        return path if path.is_file() else None

    exe = f"{name}.exe" if os.name == "nt" else name
    found = shutil.which(exe, path=env.get("PATH"))
    if found:
        return Path(found)
    for directory in _search_dirs(env):
        candidate = directory / exe
        if candidate.is_file():
            return candidate
    return None


def find_cli(env: Mapping[str, str] | None = None) -> Path | None:
    """`kicad-cli` 的路径，找不到返回 None。"""
    return _locate("kicad-cli", CLI_ENV, env)


def find_python(env: Mapping[str, str] | None = None) -> Path | None:
    """能 `import pcbnew` 的 Python 解释器路径。

    Windows 上是 KiCad 自带的 `bin/python.exe`；Linux 包管理器装的 KiCad 会把
    `pcbnew` 装进系统 Python，此时直接用当前解释器。
    """
    found = _locate("python", PYTHON_ENV, env)
    if found and os.name == "nt":
        # PATH 上的系统 Python 没有 pcbnew，只认 KiCad 目录下的那个
        if "kicad" not in str(found).lower():
            found = None
    if found:
        return found
    if os.name != "nt":
        try:
            import pcbnew  # noqa: F401
        except ImportError:
            return None
        return Path(sys.executable)
    cli = find_cli(env)
    if cli:
        candidate = cli.parent / "python.exe"
        if candidate.is_file():
            return candidate
    return None


def require_cli(env: Mapping[str, str] | None = None) -> Path:
    """拿到 `kicad-cli`，没有就报错并给出可执行的解决办法。"""
    cli = find_cli(env)
    if cli:
        return cli
    raise KicadError(
        "未找到 kicad-cli。请安装 KiCad（Windows: `winget install KiCad.KiCad`，"
        "Linux: 发行版包管理器，macOS: `brew install --cask kicad`），"
        f"或用环境变量 {CLI_ENV} 指向 kicad-cli 可执行文件。"
    )


def require_python(env: Mapping[str, str] | None = None) -> Path:
    """拿到能 import pcbnew 的解释器。"""
    python = find_python(env)
    if python:
        return python
    raise KicadError(
        "未找到能 import pcbnew 的 Python。它随 KiCad 一起安装（Windows 在 "
        f"KiCad 的 bin/python.exe），也可用环境变量 {PYTHON_ENV} 指定。"
    )


def _exec(argv: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=None if cwd is None else str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def run(args: Sequence[str | Path], *, cwd: Path | None = None,
        timeout: int = TIMEOUT, check: bool = False) -> subprocess.CompletedProcess:
    """调用 `kicad-cli <args...>`。`check=True` 时非零退出码抛 `KicadError`。"""
    cli = require_cli()
    argv = [str(cli), *(str(a) for a in args)]
    proc = _exec(argv, cwd, timeout)
    if check and proc.returncode != 0:
        raise KicadError(
            f"kicad-cli {' '.join(str(a) for a in args)} 失败（退出码 "
            f"{proc.returncode}）：\n{proc.stdout}\n{proc.stderr}".strip()
        )
    return proc


def run_python(args: Sequence[str | Path], *, cwd: Path | None = None,
               timeout: int = TIMEOUT, check: bool = False) -> subprocess.CompletedProcess:
    """用 KiCad 自带 Python 执行脚本（`pcbnew` 只在那里能 import）。"""
    python = require_python()
    argv = [str(python), *(str(a) for a in args)]
    env_utf8 = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        argv, cwd=None if cwd is None else str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, env=env_utf8,
    )
    if check and proc.returncode != 0:
        raise KicadError(
            f"KiCad Python 执行失败（退出码 {proc.returncode}）：\n"
            f"{proc.stdout}\n{proc.stderr}".strip()
        )
    return proc

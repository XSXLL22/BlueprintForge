"""示例电路的加载器 —— 单测与 `examples/` 之间唯一的一道门。

为什么要有这个文件：示例电路（4 位计数器）以前是**字符串常量**，抄在
`tests/test_design.py` 与 `tests/test_pcb_synth74.py` 两处。两份抄件会各自漂移，
而且 `examples/` 里拿不到一份能直接 `python -m hdc` 跑的文件。

现在反过来：`examples/counter/*.v` 是唯一事实来源，测试从这里读。于是

* 示例是**用户可运行的**（`python -m hdc examples/counter/counter.v --pcb`），
* 测试验的就是用户手上那一份，不是它的一份抄件，
* 工具（`hdc/`）里一个字节的示例电路都没有 —— 示例是数据，不是实现。

只读，不写。任何测试都不该改 `examples/` 下的文件；要改的变体在内存里 `replace`。
"""
from pathlib import Path

#: 仓库根目录下的 `examples/`。`__file__` 是 `<repo>/tests/examples.py`。
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

#: 基准示例：4 位计数器（异步低复位）。见 `examples/counter/README.md`。
COUNTER_DIR = EXAMPLES / "counter"
COUNTER_V = COUNTER_DIR / "counter.v"
COUNTER_TB_V = COUNTER_DIR / "counter_tb.v"


def _read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(
            f"示例文件缺失：{path}\n"
            "示例是测试的事实来源，不能删。若确实要改示例，改完重跑全量测试。")
    return path.read_text(encoding="utf-8")


def counter_rtl() -> str:
    """`examples/counter/counter.v` 的正文。"""
    return _read(COUNTER_V)


def counter_tb() -> str:
    """`examples/counter/counter_tb.v` 的正文（自检 TB，顶层 `tb_counter`）。"""
    return _read(COUNTER_TB_V)

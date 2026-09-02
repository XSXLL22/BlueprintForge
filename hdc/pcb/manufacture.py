"""T1.6 —— 制造文件导出与嘉立创（JLCPCB）格式转换。

## 分工

导出这一步没什么算法，难的是**格式细节**：错一个符号，贴片机就把元件装反。所以
这里切成两半 ——

* `to_jlcpcb_cpl()` / `to_jlcpcb_bom()`：纯文本 → 纯文本，不碰 KiCad，单测直接喂
  字符串。所有嘉立创的格式知识都在这两个函数里。
* `export_fabrication()`：调 `kicad-cli` 拿到原始 CSV / Gerber / 钻孔，交给上面两个
  函数转换，再打包。它只是适配器。

## Mid Y 到底要不要取负

任务清单写的是「Mid Y 取反」。实测**不能再取反**：

    板文件里 U1 摆在 (16.51, 17.78)
    kicad-cli pcb export pos  →  PosX=16.510000  PosY=-17.780000
    counter.drl（同一颗芯片的 1 脚孔） →  X16.51Y-17.78

KiCad 自己的 pos 文件已经是 **Gerber 坐标系**（Y 轴朝上）了，与钻孔文件逐字一致，
正是嘉立创要的。再取一次负会把整块板的元件上下镜像。「Y 取反」这条经验来自
Altium/Eagle 的坐标导出，或来自用 pcbnew `GetPosition()` 直接读板坐标（Y 轴朝下）
的脚本 —— 走 `kicad-cli` 这条路时那一步已经做完了。

`tests/test_pcb_manufacture.py` 拿钻孔文件里的孔坐标反过来校验 CPL，就是为了把这
个判断钉住：真要写反了，CPL 的 Y 全是正数而钻孔全是负数，测试立刻红。

## 旋转归一化

KiCad 读板子时把角度折进 (−180, 180]，所以 270° 回读是 **−90**（实测）。嘉立创要
[0, 360)，于是统一 `% 360`。
"""
from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from hdc.pcb import kicad

#: 双层板要给嘉立创的 7 个 Gerber 层。
GERBER_LAYERS = ("F.Cu", "B.Cu", "F.SilkS", "B.SilkS", "F.Mask", "B.Mask",
                 "Edge.Cuts")

#: 板子预览 PDF 画哪些层（人看的，不参与制造）。
BOARD_PDF_LAYERS = ("F.Cu", "B.Cu", "F.SilkS", "Edge.Cuts")

#: KiCad pos 列名 → 嘉立创 CPL 列名。顺序就是输出顺序。
CPL_COLUMNS = (("Ref", "Designator"), ("Val", "Val"), ("Package", "Package"),
               ("PosX", "Mid X"), ("PosY", "Mid Y"), ("Rot", "Rotation"),
               ("Side", "Layer"))

#: KiCad BOM 列名 → 嘉立创 BOM 列名。
BOM_COLUMNS = (("Value", "Comment"), ("Refs", "Designator"),
               ("Footprint", "Footprint"), ("Qty", "Quantity"))

#: KiCad 的 `Side` → 嘉立创的 `Layer`。
SIDES = {"top": "Top", "bottom": "Bottom"}

#: 打包出来的 ZIP 名字后缀。
ARCHIVE_SUFFIX = "_jlcpcb.zip"

#: ZIP 里的时间戳写死，同样输入得到同样字节（Gerber 正文自带时间戳，另说）。
_EPOCH = (1980, 1, 1, 0, 0, 0)


class FabError(RuntimeError):
    """制造文件导出或格式转换失败。"""


@dataclass(frozen=True)
class DrcReport:
    """`kicad-cli pcb drc` 的结论。出错也要留下报告全文，别只丢一个 False。"""

    ok: bool
    path: Path
    text: str

    @property
    def violations(self) -> tuple[str, ...]:
        """报告里每条违规的标题行，用于摘要打印。"""
        return tuple(line.strip() for line in self.text.splitlines()
                     if line.strip().startswith("["))


def check_drc(board: Path, *, report: Path | None = None) -> DrcReport:
    """让 KiCad 自己判卷：设计规则检查。这是「能不能送厂」的权威判据。"""
    board = Path(board)
    path = Path(report) if report is not None else board.with_name("drc.rpt")
    proc = kicad.run(["pcb", "drc", "--exit-code-violations",
                      "--severity-error", "-o", path, board])
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if proc.returncode not in (0, 5):        # 5 = 有违规；其它是真的跑挂了
        raise FabError(f"DRC 没跑起来（退出码 {proc.returncode}）：\n"
                       f"{proc.stdout}\n{proc.stderr}".strip())
    return DrcReport(ok=proc.returncode == 0, path=path, text=text)


@dataclass(frozen=True)
class FabOptions:
    """导出参数。默认值按嘉立创双层板的上传要求来定。"""

    gerber_layers: tuple[str, ...] = GERBER_LAYERS
    board_pdf_layers: tuple[str, ...] = BOARD_PDF_LAYERS
    #: Gerber 与钻孔文件放在 `<out_dir>/<gerber_dir>/`，整个目录就是要上传的 ZIP。
    gerber_dir: str = "gerber"


@dataclass(frozen=True)
class Fabrication:
    """一套制造文件。路径全是绝对的，调用方拿去检查或上传。"""

    project: str
    gerbers: tuple[Path, ...]
    drill: Path
    bom: Path
    cpl: Path
    schematic_pdf: Path
    board_pdf: Path
    archive: Path

    @property
    def files(self) -> tuple[Path, ...]:
        """全部产物，顺序稳定 —— 用来一次性检查「该有的都有」。"""
        return (*self.gerbers, self.drill, self.bom, self.cpl,
                self.schematic_pdf, self.board_pdf, self.archive)


# --- 纯格式转换（不需要装 KiCad） --------------------------------------------

def _table(text: str, what: str) -> tuple[list[str], list[list[str]]]:
    """CSV 文本 → (表头, 数据行)。KiCad 会把字段加引号，交给 `csv` 模块处理。"""
    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    if not rows:
        raise FabError(f"{what} 是空的，KiCad 那一步多半失败了")
    return [cell.strip() for cell in rows[0]], rows[1:]


def _pick(header: list[str], columns, what: str) -> list[int]:
    """按列名取下标。缺列就报错并指名道姓，别让错误漂到下游。"""
    missing = [name for name, _ in columns if name not in header]
    if missing:
        raise FabError(f"{what} 缺少列 {missing}，实际表头是 {header}")
    return [header.index(name) for name, _ in columns]


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _dump(header: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def _mm(value: str, what: str) -> str:
    """坐标：嘉立创按毫米读，带上单位后缀就不会被当成 mil 或 inch。"""
    try:
        return f"{float(value):.4f}mm"
    except ValueError as exc:
        raise FabError(f"{what} 不是数字：{value!r}") from exc


def _rotation(value: str) -> str:
    """归一化到 [0, 360)。KiCad 会把 270° 写成 −90，直接交给嘉立创就是装反 180°。"""
    try:
        return f"{float(value) % 360:.2f}"
    except ValueError as exc:
        raise FabError(f"旋转角不是数字：{value!r}") from exc


def _side(value: str) -> str:
    found = SIDES.get(value.strip().lower())
    if found is None:
        raise FabError(f"认不出的板面 {value!r}，只认 {sorted(SIDES)}")
    return found


def to_jlcpcb_cpl(text: str) -> str:
    """`kicad-cli pcb export pos --format csv --units mm` 的输出 → 嘉立创 CPL。

    改的只有三处：表头换名、坐标补 `mm` 后缀、旋转归一化到 [0, 360)、板面首字母
    大写。**坐标数值一个字都不改** —— KiCad 的 pos 文件已经是 Gerber 坐标系，理由
    见模块开头。
    """
    header, rows = _table(text, "位置文件（pos）")
    index = _pick(header, CPL_COLUMNS, "位置文件（pos）")
    spot = {name: index[i] for i, (name, _) in enumerate(CPL_COLUMNS)}
    out = []
    for row in rows:
        ref = _cell(row, spot["Ref"])
        out.append([ref,
                    _cell(row, spot["Val"]),
                    _cell(row, spot["Package"]),
                    _mm(_cell(row, spot["PosX"]), f"{ref} 的 X"),
                    _mm(_cell(row, spot["PosY"]), f"{ref} 的 Y"),
                    _rotation(_cell(row, spot["Rot"])),
                    _side(_cell(row, spot["Side"]))])
    return _dump([label for _, label in CPL_COLUMNS], out)


def to_jlcpcb_bom(text: str) -> str:
    """`kicad-cli sch export bom` 的输出 → 嘉立创 BOM。

    除了换表头，还把封装名前面的库名去掉（`Package_DIP:DIP-20_W7.62mm` →
    `DIP-20_W7.62mm`）：嘉立创拿这一列去匹配自家元件库，库名前缀只会干扰匹配，
    去掉之后也与 CPL 的 `Package` 列对得上。
    """
    header, rows = _table(text, "物料清单（BOM）")
    index = _pick(header, BOM_COLUMNS, "物料清单（BOM）")
    spot = {name: index[i] for i, (name, _) in enumerate(BOM_COLUMNS)}
    out = []
    for row in rows:
        footprint = _cell(row, spot["Footprint"])
        out.append([_cell(row, spot["Value"]),
                    _cell(row, spot["Refs"]),
                    footprint.rsplit(":", 1)[-1],
                    _cell(row, spot["Qty"])])
    return _dump([label for _, label in BOM_COLUMNS], out)


# --- 调 kicad-cli 拿原始产物 --------------------------------------------------

def _gerbers(board: Path, into: Path, opts: FabOptions) -> None:
    """7 个 Gerber 层。`--check-zones` 是保险：铺铜没填过就先填再画。"""
    kicad.run(["pcb", "export", "gerbers",
               "--layers", ",".join(opts.gerber_layers),
               "--subtract-soldermask", "--check-zones",
               "-o", into, board], check=True)


def _drill(board: Path, into: Path) -> None:
    """Excellon 钻孔文件，毫米、绝对原点 —— 与 Gerber 同一个坐标系。

    PTH 与 NPTH 合成一个文件（嘉立创接受），不生成钻孔图 PDF：那是给人看的，
    混进要上传的 ZIP 里只会让对方的 Gerber 解析器多一个问号。
    """
    kicad.run(["pcb", "export", "drill", "--format", "excellon",
               "--excellon-units", "mm", "--drill-origin", "absolute",
               "--excellon-zeros-format", "decimal",
               "-o", into, board], check=True)


def _raw_cpl(board: Path, into: Path) -> str:
    """贴片坐标原始 CSV。不加 `--exclude-fp-th`：这块板全是插件件，排除完就空了。"""
    path = into / "pos-kicad.csv"
    kicad.run(["pcb", "export", "pos", "--format", "csv", "--units", "mm",
               "--side", "both", "-o", path, board], check=True)
    return path.read_text(encoding="utf-8")


def _raw_bom(schematic: Path, into: Path) -> str:
    """物料清单原始 CSV。

    分组交给 KiCad（同值同封装并成一行，位号逗号相连）；`--ref-range-delimiter ''`
    关掉区间缩写 —— 嘉立创要 `C3,C4,C5`，看不懂 `C3-C5`。
    """
    path = into / "bom-kicad.csv"
    kicad.run(["sch", "export", "bom",
               "--fields", "Reference,Value,Footprint,QUANTITY",
               "--labels", "Refs,Value,Footprint,Qty",
               "--group-by", "Value,Footprint",
               "--ref-range-delimiter", "",
               "-o", path, schematic], check=True)
    return path.read_text(encoding="utf-8")


def _pdfs(board: Path, schematic: Path, into: Path,
          opts: FabOptions) -> tuple[Path, Path]:
    """两张给人看的图：原理图与板子叠层预览。"""
    sch_pdf, pcb_pdf = into / "schematic.pdf", into / "board.pdf"
    kicad.run(["sch", "export", "pdf", "-o", sch_pdf, schematic], check=True)
    kicad.run(["pcb", "export", "pdf", "--mode-single", "--include-border-title",
               "--layers", ",".join(opts.board_pdf_layers),
               "-o", pcb_pdf, board], check=True)
    return sch_pdf, pcb_pdf


def _archive(files: list[Path], path: Path) -> Path:
    """把 Gerber 与钻孔打成 ZIP：平铺在根目录，时间戳写死。

    嘉立创要求 Gerber 在 ZIP 根目录或单一子目录里；平铺最不容易出岔子。时间戳写死
    是为了同样的输入得到同样的字节（Gerber 正文里 KiCad 自带的时间戳另说）。
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(files, key=lambda p: p.name):
            info = zipfile.ZipInfo(item.name, date_time=_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, item.read_bytes())
    return path


# --- 对外接口 ---------------------------------------------------------------

def export_fabrication(*, board: Path, schematic: Path, out_dir: Path,
                       options: FabOptions | None = None) -> Fabrication:
    """导出全套制造文件并打包，返回每个产物的路径。

    产出（`out_dir` 下）：`gerber/`（7 层 + `.drl` + `.gbrjob`）、`bom.csv`、
    `cpl.csv`、`schematic.pdf`、`board.pdf`、`<项目名>_jlcpcb.zip`。
    """
    opts = options or FabOptions()
    board, schematic = Path(board), Path(schematic)
    if not board.is_file():
        raise FabError(f"板文件不存在：{board}")
    if not schematic.is_file():
        raise FabError(f"原理图文件不存在：{schematic}")
    out_dir = Path(out_dir)
    gerber_dir = out_dir / opts.gerber_dir
    gerber_dir.mkdir(parents=True, exist_ok=True)

    _gerbers(board, gerber_dir, opts)
    _drill(board, gerber_dir)
    drills = sorted(gerber_dir.glob("*.drl"))
    if not drills:
        raise FabError(f"钻孔文件没生成：{gerber_dir}")
    gerbers = tuple(sorted(p for p in gerber_dir.iterdir()
                           if p.suffix.lower() not in (".drl", ".zip")))

    cpl = out_dir / "cpl.csv"
    cpl.write_text(to_jlcpcb_cpl(_raw_cpl(board, out_dir)), encoding="utf-8")
    bom = out_dir / "bom.csv"
    bom.write_text(to_jlcpcb_bom(_raw_bom(schematic, out_dir)), encoding="utf-8")
    for scratch in ("pos-kicad.csv", "bom-kicad.csv"):
        (out_dir / scratch).unlink(missing_ok=True)

    sch_pdf, pcb_pdf = _pdfs(board, schematic, out_dir, opts)
    project = board.stem
    archive = _archive([*gerbers, *drills],
                       out_dir / f"{project}{ARCHIVE_SUFFIX}")
    return Fabrication(project=project, gerbers=gerbers, drill=drills[0],
                       bom=bom, cpl=cpl, schematic_pdf=sch_pdf,
                       board_pdf=pcb_pdf, archive=archive)

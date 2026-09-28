"""
模块组合 CAD 图自动生成。

流程：
    1. 从数据库 ProductInfo.module_cad 表读取各模块的正面图
       （DXF 内容，zlib 压缩存储；由 import_cad_to_db.py 从本地 DWG 导入）；
    2. 解压后落到 dxf_cache_dir 本地缓存文件；
    3. 用 ezdxf 把选型结果中的各模块（按数量）拼装成一张组合图；
    4. 输出 DXF，并（若 ODA 可用）转换成 DWG。

数据库连接复用 db_config.json；输出目录、排版参数在 cad_config.json 中配置，
相对路径一律相对本文件所在目录解析。
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import ezdxf
import mysql.connector
from ezdxf import bbox
from ezdxf.addons.importer import Importer
from ezdxf.enums import TextEntityAlignment

from selector_core import load_db_config

PROGRAM_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROGRAM_DIR / "cad_config.json"

CAD_TABLE = "module_cad"

DEFAULT_CONFIG: Dict[str, Any] = {
    "cad_source_dir": "../../7000模块正面CAD",
    "dxf_cache_dir": "cad_cache_dxf",
    "output_dir": "cad_output",
    "oda_converter_path": "../../ODAExtract/ODAFileConverter.exe",
    "modules_per_row": 16,
    "module_gap_mm": 7.5,
    "row_gap_mm": 40,
    "margin_mm": 40,
    "dwg_version": "ACAD2018",
    "keep_dxf": True,
}

TEXT_STYLE = "HZ_TEXT"


class CadLayoutError(Exception):
    """可以直接显示给用户的 CAD 生成错误。"""


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (PROGRAM_DIR / path).resolve()


def load_cad_config() -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)

    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as file:
                user_config = json.load(file)
            if isinstance(user_config, dict):
                config.update(user_config)
        except (OSError, json.JSONDecodeError) as exc:
            raise CadLayoutError(f"读取 cad_config.json 失败：{exc}")
    else:
        try:
            with CONFIG_PATH.open("w", encoding="utf-8") as file:
                json.dump(config, file, ensure_ascii=False, indent=4)
        except OSError:
            pass

    return config


def find_oda_converter(config: Dict[str, Any]) -> Optional[Path]:
    """按配置、常见安装位置、PATH 的顺序查找 ODA File Converter。"""
    candidates: List[Path] = []

    configured = config.get("oda_converter_path")
    if configured:
        candidates.append(_resolve_path(str(configured)))

    candidates.append((PROGRAM_DIR / ".." / ".." / "ODAExtract" / "ODAFileConverter.exe").resolve())

    for base in (r"C:\Program Files\ODA", r"C:\Program Files (x86)\ODA"):
        root = Path(base)
        if root.is_dir():
            candidates.extend(root.glob("*/ODAFileConverter.exe"))
            candidates.extend(root.glob("ODAFileConverter*/ODAFileConverter.exe"))

    which = shutil.which("ODAFileConverter")
    if which:
        candidates.append(Path(which))

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _run_oda_converter(
    exe: Path,
    src_dir: Path,
    dst_dir: Path,
    output_version: str,
    output_type: str,
    file_filter: str,
) -> bool:
    """
    ODA File Converter 命令行：
        ODAFileConverter <src> <dst> <version> <type> <recurse> <audit> [filter]
    """
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0

    try:
        process = subprocess.run(
            [
                str(exe),
                str(src_dir),
                str(dst_dir),
                output_version,
                output_type,
                "0",
                "1",
                file_filter,
            ],
            startupinfo=startupinfo,
            timeout=600,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CadLayoutError(f"运行 ODA File Converter 失败：{exc}")

    return process.returncode == 0


def convert_local_dwgs_to_dxf(
    config: Dict[str, Any],
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Path]:
    """
    本地导入工具使用：把 CAD 源目录中每个 DWG 转成 DXF 缓存文件。

    返回 {模块名小写: DXF路径}。只在 DWG 比 DXF 新或 DXF 缺失时才重新转换。
    """
    src_dir = _resolve_path(str(config["cad_source_dir"]))
    if not src_dir.is_dir():
        raise CadLayoutError(
            f"找不到模块 CAD 源目录：{src_dir}。"
            "请检查 cad_config.json 中的 cad_source_dir。"
        )

    cache_dir = _resolve_path(str(config["dxf_cache_dir"]))
    cache_dir.mkdir(parents=True, exist_ok=True)

    dwg_map = {
        path.stem.lower(): path
        for path in src_dir.glob("*.dwg")
    }
    if not dwg_map:
        raise CadLayoutError(f"CAD 源目录中没有 DWG 文件：{src_dir}")

    pending: List[Path] = []
    for stem, dwg_path in sorted(dwg_map.items()):
        dxf_path = cache_dir / f"{stem}.dxf"
        if (
            not dxf_path.exists()
            or dxf_path.stat().st_mtime < dwg_path.stat().st_mtime
        ):
            pending.append(dwg_path)

    if pending:
        oda_exe = find_oda_converter(config)
        if oda_exe is None:
            missing = "、".join(path.stem for path in pending[:8])
            if len(pending) > 8:
                missing += f" 等 {len(pending)} 个"
            raise CadLayoutError(
                "以下模块缺少 DXF 缓存且找不到 ODA File Converter，"
                f"无法转换：{missing}。"
                "请在 cad_config.json 的 oda_converter_path 中配置 "
                "ODAFileConverter.exe 的位置。"
            )

        if progress:
            progress(f"正在转换 {len(pending)} 个模块 DWG → DXF……")

        pending_dir = cache_dir / "_pending"
        pending_dir.mkdir(exist_ok=True)
        try:
            for dwg_path in pending:
                shutil.copy2(dwg_path, pending_dir / dwg_path.name)

            ok = _run_oda_converter(
                oda_exe,
                pending_dir,
                cache_dir,
                "ACAD2018",
                "DXF",
                "*.dwg",
            )
            if not ok:
                raise CadLayoutError("ODA File Converter 批量转换 DWG 失败。")
        finally:
            shutil.rmtree(pending_dir, ignore_errors=True)

    dxf_map = {
        stem: cache_dir / f"{stem}.dxf"
        for stem in dwg_map
        if (cache_dir / f"{stem}.dxf").exists()
    }
    return dxf_map


def ensure_dxf_cache(
    config: Dict[str, Any],
    progress: Optional[Callable[[str], None]] = None,
    needed_names: Optional[Iterable[str]] = None,
) -> Dict[str, Path]:
    """
    从数据库 module_cad 表读取模块 DXF（zlib 压缩），
    解压后写入 dxf_cache_dir 缓存目录。

    needed_names 为需要导入的模块名集合（不区分大小写）；
    传 None 时读取全部记录。

    返回 {模块名小写: DXF路径}。
    """
    cache_dir = _resolve_path(str(config["dxf_cache_dir"]))
    cache_dir.mkdir(parents=True, exist_ok=True)

    if needed_names is None:
        wanted: List[str] = []
    else:
        wanted = sorted({name.strip().lower() for name in needed_names if name.strip()})

    if not wanted:
        return {}

    try:
        connection = mysql.connector.connect(**load_db_config())
    except mysql.connector.Error as exc:
        raise CadLayoutError(f"连接数据库失败：{exc}") from exc

    try:
        cursor = connection.cursor()
        try:
            placeholders = ", ".join(["%s"] * len(wanted))
            cursor.execute(
                f"SELECT module_name, dxf_data FROM {CAD_TABLE} "
                f"WHERE module_name IN ({placeholders})",
                wanted,
            )
            records = cursor.fetchall()
        finally:
            cursor.close()
    except mysql.connector.Error as exc:
        raise CadLayoutError(
            f"读取数据库表 {CAD_TABLE} 失败：{exc}。"
            "如果还没导入过模块 CAD，请先运行 import_cad_to_db.py。"
        ) from exc
    finally:
        connection.close()

    if progress and records:
        progress(f"正在从数据库读取 {len(records)} 个模块 CAD 图……")

    dxf_map: Dict[str, Path] = {}
    for module_name, blob in records:
        name = str(module_name).strip().lower()
        if not name or blob is None:
            continue
        try:
            dxf_bytes = zlib.decompress(bytes(blob))
        except zlib.error as exc:
            raise CadLayoutError(
                f"数据库中 {module_name} 的 CAD 数据解压失败：{exc}"
            ) from exc

        dxf_path = cache_dir / f"{name}.dxf"
        dxf_path.write_bytes(dxf_bytes)
        dxf_map[name] = dxf_path

    return dxf_map


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" ._")
    return cleaned[:80] or "unnamed"


def _sanitize_block_name(name: str) -> str:
    return "MOD_" + re.sub(r"\W+", "_", name.strip()).strip("_")


def _build_module_block(
    doc: ezdxf.document.Drawing,
    module_name: str,
    dxf_path: Path,
    width_cache: Dict[str, float],
    height_cache: Dict[str, float],
) -> Optional[str]:
    """
    把一个模块的正面图导入目标文档的块定义中，
    并平移到以 (0,0) 为左下角。返回块名；失败返回 None。
    """
    try:
        source_doc = ezdxf.readfile(dxf_path)
    except (IOError, ezdxf.DXFStructureError) as exc:
        raise CadLayoutError(f"读取模块 DXF 失败（{dxf_path.name}）：{exc}")

    source_msp = source_doc.modelspace()
    entities = list(source_msp)
    if not entities:
        return None

    extents = bbox.extents(entities, fast=True)
    if extents is None or extents.has_data is False:
        return None

    block_name = _sanitize_block_name(module_name)
    if block_name in doc.blocks:
        block_name = f"{block_name}_{len(doc.blocks)}"

    block = doc.blocks.new(name=block_name)

    importer = Importer(source_doc, doc)
    imported = []
    for entity in entities:
        new_entity = importer.import_entity(entity, block)
        if new_entity is not None:
            imported.append(new_entity)
    importer.finalize()

    for entity in imported:
        entity.translate(-extents.extmin.x, -extents.extmin.y, 0)

    width_cache[block_name] = extents.size.x
    height_cache[block_name] = extents.size.y
    return block_name


def generate_combined_cad(
    result: Dict[str, Any],
    requirement_name: str,
    progress: Optional[Callable[[str], None]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    根据选型结果生成模块正面组合 CAD 图。

    参数：
        result: select_modules / select_modules_and_save 的返回值，
                需要 rows（模块明细）、requirement、total_price、module_count。
        requirement_name: 需求名称，用于输出文件命名。

    返回：
        {
            "success": bool,
            "error": str | None,
            "dxf_path": str | None,
            "dwg_path": str | None,
            "folder": str | None,
            "placed_count": int,
            "missing_modules": [str],
            "warnings": [str],
        }
    """
    summary = {
        "success": False,
        "error": None,
        "dxf_path": None,
        "dwg_path": None,
        "folder": None,
        "placed_count": 0,
        "missing_modules": [],
        "warnings": [],
    }

    try:
        _generate(result, requirement_name, progress, config, summary)
    except CadLayoutError as exc:
        summary["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - 面向用户兜底
        summary["error"] = f"{type(exc).__name__}: {exc}"

    return summary


def _generate(
    result: Dict[str, Any],
    requirement_name: str,
    progress: Optional[Callable[[str], None]],
    config: Optional[Dict[str, Any]],
    summary: Dict[str, Any],
) -> None:
    if config is None:
        config = load_cad_config()

    rows = result.get("rows") or []
    if not rows:
        raise CadLayoutError("没有选中任何模块，无法生成组合 CAD 图。")

    def is_board_row(row: Dict[str, Any]) -> bool:
        return str(row.get("category", "")).startswith("底板")

    # 机架模式：rows 含底板行时按底板分行绘制（底板本身不画）。
    has_boards = any(is_board_row(row) for row in rows)

    board_groups: List[Dict[str, Any]] = []
    placements: List[Dict[str, Any]] = []
    missing_modules: List[str] = []

    def rows_to_items(row_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for row in row_list:
            name = str(row.get("module_name", "")).strip()
            count = int(row.get("count", 0))
            if not name or count <= 0:
                continue
            items.append(
                {
                    "name": name,
                    "cname": str(row.get("module_cname", "")),
                    "count": count,
                    "dxf": None,
                }
            )
        return items

    if has_boards:
        for row in rows:
            if is_board_row(row):
                board_groups.append({"board": row, "rows": []})
                continue
            if board_groups:
                board_groups[-1]["rows"].append(row)

        for group in board_groups:
            group["items"] = rows_to_items(group["rows"])
    else:
        board_groups.append(
            {"board": None, "rows": list(rows), "items": rows_to_items(rows)}
        )

    needed_names = {
        item["name"]
        for group in board_groups
        for item in group["items"]
    }
    if progress:
        progress("正在准备模块 CAD 图缓存……")
    dxf_map = ensure_dxf_cache(config, progress, needed_names)

    for group in board_groups:
        resolved = []
        for item in group["items"]:
            name = item["name"]
            dxf_path = dxf_map.get(name.lower())
            if dxf_path is None:
                if name not in missing_modules:
                    missing_modules.append(name)
                continue
            item["dxf"] = dxf_path
            resolved.append(item)
        group["items"] = resolved
        placements.extend(resolved)

    summary["missing_modules"] = missing_modules
    if missing_modules:
        summary["warnings"].append(
            "以下模块没有找到对应的 CAD 图，组合图中已跳过："
            + "、".join(missing_modules)
        )

    if not placements:
        raise CadLayoutError(
            "所有选中模块都没有对应的 CAD 图，无法生成组合图。"
        )

    # ------------------------------------------------------------------
    # 目标文档
    # ------------------------------------------------------------------
    if progress:
        progress("正在导入模块图形……")

    doc = ezdxf.new("R2018", setup=False)
    doc.header["$INSUNITS"] = 4  # mm
    doc.header["$MEASUREMENT"] = 1
    doc.header["$EXTMIN"] = (-50, -3000, 0)
    doc.header["$EXTMAX"] = (3000, 100, 0)
    if "HZ_TEXT" not in doc.styles:
        doc.styles.add(TEXT_STYLE, font="simsun.ttc")

    msp = doc.modelspace()

    width_cache: Dict[str, float] = {}
    height_cache: Dict[str, float] = {}
    block_names: Dict[str, str] = {}

    for placement in placements:
        name = placement["name"]
        if name in block_names:
            continue
        try:
            block_name = _build_module_block(
                doc,
                name,
                placement["dxf"],
                width_cache,
                height_cache,
            )
        except CadLayoutError as exc:
            summary["warnings"].append(str(exc))
            block_name = None
        block_names[name] = block_name  # type: ignore[assignment]

    usable = [
        placement
        for placement in placements
        if block_names.get(placement["name"])
    ]
    if not usable:
        raise CadLayoutError("模块 DXF 导入全部失败，无法生成组合图。")

    # ------------------------------------------------------------------
    # 排版
    #
    # 机架模式：每块底板占一行，行下方标注底板名称；
    # 普通模式：所有模块连续摆放，按 modules_per_row 自动换行。
    # 同一型号连续放置时在模块上方成组标注。
    # ------------------------------------------------------------------
    if progress:
        progress("正在排版组合图……")

    margin = float(config.get("margin_mm", 40))
    gap = float(config.get("module_gap_mm", 7.5))
    row_gap = float(config.get("row_gap_mm", 40))
    per_row = max(1, int(config.get("modules_per_row", 16)))

    max_height = max(height_cache.values())
    title_area = 100.0
    # 行距包含组标注（上方）和底板标注（下方）的空间。
    row_pitch = max_height + row_gap + 34

    placed_count = 0

    segments: List[Dict[str, Any]] = []
    board_labels: List[Dict[str, Any]] = []

    def row_y_bottom(row_index: int) -> float:
        return -(title_area + margin + row_index * row_pitch + max_height)

    def place_line(
        items: List[Dict[str, Any]],
        row_index: int,
        auto_wrap: bool,
    ) -> None:
        """把一组模块放到一行；auto_wrap 时超过 per_row 换到下一行。"""
        nonlocal placed_count

        x = margin
        in_row = 0
        wrap = row_index

        group_name: Optional[str] = None
        group_count = 0
        group_x_start = 0.0
        group_wrap = wrap

        def close_group() -> None:
            nonlocal group_name, group_count
            if group_name is None or group_count <= 0:
                return
            segments.append(
                {
                    "name": group_name,
                    "count": group_count,
                    "x_start": group_x_start,
                    "x_end": x - gap,
                    "row": group_wrap,
                }
            )
            group_name = None
            group_count = 0

        for item in items:
            name = item["name"]
            block_name = block_names[name]
            width = width_cache[block_name]

            for _ in range(item["count"]):
                if auto_wrap and in_row >= per_row:
                    close_group()
                    x = margin
                    wrap += 1
                    in_row = 0

                if group_name != name or group_wrap != wrap:
                    close_group()
                    group_name = name
                    group_count = 0
                    group_x_start = x
                    group_wrap = wrap

                msp.add_blockref(
                    block_name,
                    insert=(x, row_y_bottom(wrap)),
                    dxfattribs={"layer": "0"},
                )

                x += width + gap
                in_row += 1
                group_count += 1
                placed_count += 1

        close_group()

    if has_boards:
        for group in board_groups:
            items = [
                item for item in group["items"] if block_names.get(item["name"])
            ]
            if not items:
                continue

            row_index = len(board_labels)
            place_line(items, row_index, auto_wrap=False)

            board = group["board"]
            board_labels.append(
                {
                    "text": (
                        f"底板{board.get('board', row_index + 1)}："
                        f"{board.get('module_name', '')}"
                        f"（{board.get('slots', '?')} 槽位）"
                    ),
                    "row": row_index,
                }
            )
    else:
        place_line(placements, 0, auto_wrap=True)

    summary["placed_count"] = placed_count

    # ------------------------------------------------------------------
    # 组标注 + 标题栏
    # ------------------------------------------------------------------
    for segment in segments:
        center_x = (segment["x_start"] + segment["x_end"]) / 2
        y_bottom = -(title_area + margin + segment["row"] * row_pitch + max_height)
        label_y = y_bottom + max_height + 8

        msp.add_text(
            f"{segment['name']} ×{segment['count']}",
            dxfattribs={
                "style": TEXT_STYLE,
                "height": 7,
                "layer": "TEXT_LABEL",
            },
        ).set_placement(
            (center_x, label_y),
            align=TextEntityAlignment.BOTTOM_CENTER,
        )

    for label in board_labels:
        y_bottom = row_y_bottom(label["row"])
        msp.add_text(
            label["text"],
            dxfattribs={
                "style": TEXT_STYLE,
                "height": 9,
                "layer": "TEXT_LABEL",
            },
        ).set_placement(
            (margin, y_bottom - 18),
            align=TextEntityAlignment.BOTTOM_LEFT,
        )

    requirement = result.get("requirement", {})
    now_text = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    count_note = ""
    if missing_modules:
        count_note = (
            f"（其中 {len(missing_modules)} 个模块缺少 CAD 图，未绘制："
            + "、".join(missing_modules)
            + "）"
        )

    title_lines = [
        (f"PLC 模块正面组合图  ——  {requirement_name}", 14),
        (
            "IO 需求："
            f"AI={requirement.get('ai', 0)}，AO={requirement.get('ao', 0)}，"
            f"DI={requirement.get('di', 0)}，DO={requirement.get('do', 0)}",
            7,
        ),
        (
            f"模块总数：{result.get('module_count', 0)}    "
            f"图中绘制：{placed_count}    "
            f"总价格：{result.get('total_price', 0):.2f}    "
            f"生成时间：{now_text}",
            7,
        ),
    ]

    if count_note:
        title_lines.append((count_note, 6))

    for index, (line, height) in enumerate(title_lines):
        msp.add_text(
            line,
            dxfattribs={
                "style": TEXT_STYLE,
                "height": height,
                "layer": "TEXT_TITLE",
            },
        ).set_placement(
            (margin, -index * 22),
            align=TextEntityAlignment.BOTTOM_LEFT,
        )

    # ------------------------------------------------------------------
    # 保存 DXF 并转换为 DWG
    # ------------------------------------------------------------------
    if progress:
        progress("正在保存组合图……")

    output_root = _resolve_path(str(config["output_dir"]))
    safe_name = _sanitize_filename(requirement_name)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_folder = output_root / f"{safe_name}_{timestamp}"
    run_folder.mkdir(parents=True, exist_ok=True)

    # 用 ASCII 临时文件名做转换，避免转换工具对非 ASCII 文件名的兼容问题。
    temp_dxf = run_folder / "layout.dxf"
    doc.saveas(temp_dxf)

    oda_exe = find_oda_converter(config)
    dwg_file: Optional[Path] = None

    if oda_exe is None:
        summary["warnings"].append(
            "未找到 ODA File Converter，只输出了 DXF 格式。"
            "DXF 可以直接用 AutoCAD 等软件打开。"
        )
    else:
        if progress:
            progress("正在转换 DWG 格式……")

        ok = _run_oda_converter(
            oda_exe,
            run_folder,
            run_folder,
            str(config.get("dwg_version", "ACAD2018")),
            "DWG",
            "*.dxf",
        )

        temp_dwg = run_folder / "layout.dwg"
        if ok and temp_dwg.exists():
            dwg_file = temp_dwg
        else:
            summary["warnings"].append("DXF → DWG 转换失败，输出保留 DXF 格式。")

    final_dxf: Optional[Path] = None
    if bool(config.get("keep_dxf", True)):
        final_dxf = run_folder / f"{safe_name}_模块组合图.dxf"
        shutil.move(str(temp_dxf), final_dxf)
    else:
        temp_dxf.unlink(missing_ok=True)

    if dwg_file is not None:
        final_dwg = run_folder / f"{safe_name}_模块组合图.dwg"
        shutil.move(str(dwg_file), final_dwg)
        dwg_file = final_dwg

    summary["success"] = True
    summary["folder"] = str(run_folder)
    summary["dxf_path"] = str(final_dxf) if final_dxf else None
    summary["dwg_path"] = str(dwg_file) if dwg_file else None

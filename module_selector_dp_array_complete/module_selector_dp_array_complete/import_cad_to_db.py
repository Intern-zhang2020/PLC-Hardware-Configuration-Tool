"""
把本地“7000模块正面CAD”目录中的 DWG 图纸导入数据库表 module_cad。

流程：
    1. 通过 ODA File Converter 把 DWG 转成 DXF（复用 cad_cache_dxf 缓存）；
    2. DXF 内容 zlib 压缩后写入 ProductInfo.module_cad 表
       （module_name 主键，dxf_data MEDIUMBLOB）；
    3. 删除数据库中本地已不存在的模块记录。

运行：
    python import_cad_to_db.py

之后生成组合 CAD 图时（cad_layout.py）不再读取本地 DWG，
全部从数据库读取。
"""

from __future__ import annotations

import sys
import zlib

import mysql.connector

from cad_layout import CAD_TABLE, CadLayoutError, convert_local_dwgs_to_dxf, load_cad_config
from selector_core import load_db_config

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {CAD_TABLE} (
    module_name VARCHAR(64) NOT NULL,
    dxf_data MEDIUMBLOB NOT NULL,
    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (module_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
COMMENT='模块正面 CAD 图（DXF 内容，zlib 压缩）'
"""


def main() -> int:
    try:
        config = load_cad_config()
        dxf_map = convert_local_dwgs_to_dxf(config, print)
    except CadLayoutError as exc:
        print(f"导入失败：{exc}")
        return 1

    if not dxf_map:
        print("没有可导入的 DXF 文件。")
        return 1

    total_raw = 0
    total_compressed = 0
    imported: list[str] = []

    try:
        connection = mysql.connector.connect(**load_db_config())
    except mysql.connector.Error as exc:
        print(f"连接数据库失败：{exc}")
        return 1

    try:
        cursor = connection.cursor()
        try:
            cursor.execute(CREATE_TABLE_SQL)

            for name, dxf_path in sorted(dxf_map.items()):
                raw = dxf_path.read_bytes()
                compressed = zlib.compress(raw, 6)
                cursor.execute(
                    f"REPLACE INTO {CAD_TABLE} (module_name, dxf_data) "
                    "VALUES (%s, %s)",
                    (name, compressed),
                )
                imported.append(name)
                total_raw += len(raw)
                total_compressed += len(compressed)

            placeholders = ", ".join(["%s"] * len(imported))
            cursor.execute(
                f"DELETE FROM {CAD_TABLE} WHERE module_name NOT IN ({placeholders})",
                imported,
            )
            removed = cursor.rowcount

            connection.commit()

            cursor.execute(f"SELECT COUNT(*) FROM {CAD_TABLE}")
            remaining = cursor.fetchone()[0]
        finally:
            cursor.close()
    except mysql.connector.Error as exc:
        print(f"写入数据库失败：{exc}")
        return 1
    finally:
        connection.close()

    print(f"已导入 {len(imported)} 个模块 CAD 图：")
    for name in imported:
        print(f"  {name}")
    if removed:
        print(f"已删除数据库中本地不存在的 {removed} 条记录。")
    print(
        f"原始 DXF 共 {total_raw / 1024 / 1024:.1f} MB，"
        f"压缩后 {total_compressed / 1024 / 1024:.1f} MB，"
        f"表内现有 {remaining} 条记录。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

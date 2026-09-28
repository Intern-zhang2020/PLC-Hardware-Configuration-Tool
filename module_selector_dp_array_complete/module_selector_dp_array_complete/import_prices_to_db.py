"""
把模块单价 CSV 导入数据库 ProductInfo.base 表。

用法：
    python import_prices_to_db.py                     # 使用默认 module_prices.csv
    python import_prices_to_db.py <单价文件.csv>       # 指定其他文件

CSV 格式（UTF-8，首行为表头）：
    模块型号,单价
    MPU710,4868.70

导入完成后会列出：
    - 更新成功的模块数量；
    - CSV 中数据库不存在的型号；
    - 数据库中仍无单价（NULL 或 0）的全部模块。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import mysql.connector

from selector_core import load_db_config

DEFAULT_CSV = Path(__file__).resolve().parent / "module_prices.csv"


def load_prices(csv_path: Path) -> dict[str, float]:
    prices: dict[str, float] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        for row in reader:
            if not row:
                continue
            name = row[0].strip()
            if not name or name in ("模块型号", "型号", "module_name"):
                continue
            if len(row) < 2:
                raise ValueError(f"第 {reader.line_num} 行缺少单价：{row}")
            price_text = row[1].strip().replace("￥", "").replace("¥", "")
            try:
                price = round(float(price_text), 2)
            except ValueError as exc:
                raise ValueError(
                    f"第 {reader.line_num} 行单价无法解析：{row[1]!r}"
                ) from exc
            if price < 0:
                raise ValueError(f"第 {reader.line_num} 行单价为负：{row[1]!r}")
            prices[name.upper()] = price
    return prices


def main() -> int:
    if len(sys.argv) > 1:
        csv_path = Path(sys.argv[1])
    else:
        csv_path = DEFAULT_CSV

    if not csv_path.is_file():
        print(f"找不到单价文件：{csv_path}")
        return 1

    try:
        prices = load_prices(csv_path)
    except ValueError as exc:
        print(f"解析 {csv_path.name} 失败：{exc}")
        return 1

    print(f"单价文件共 {len(prices)} 个模块，开始导入……")

    try:
        connection = mysql.connector.connect(**load_db_config())
    except mysql.connector.Error as exc:
        print(f"连接数据库失败：{exc}")
        return 1

    try:
        cursor = connection.cursor()
        existing: set[str] = set()
        try:
            cursor.execute("SELECT module_name FROM base")
            existing = {str(row[0]).upper() for row in cursor.fetchall()}

            not_found = [
                name for name in sorted(prices) if name not in existing
            ]
            to_update = [
                (price, name)
                for name, price in sorted(prices.items())
                if name in existing
            ]
            cursor.executemany(
                "UPDATE base SET module_price = %s WHERE module_name = %s",
                to_update,
            )
            connection.commit()
        finally:
            cursor.close()
    except mysql.connector.Error as exc:
        print(f"写入数据库失败：{exc}")
        return 1
    finally:
        connection.close()

    updated = [name for _, name in to_update]
    print(f"已更新 {len(updated)} 个模块的单价。")
    if not_found:
        print("\nCSV 中有、但数据库 base 表不存在的型号：")
        for name in not_found:
            print(f"  {name}")

    try:
        connection = mysql.connector.connect(**load_db_config())
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT module_name, module_cname, module_price "
                "FROM base ORDER BY module_name"
            )
            no_price = [
                (name, cname)
                for name, cname, price in cursor.fetchall()
                if price is None or float(price) == 0
            ]
        finally:
            cursor.close()
        connection.close()
    except mysql.connector.Error as exc:
        print(f"查询无单价模块失败：{exc}")
        return 1

    print("\n数据库中仍无单价的模块：")
    if no_price:
        for name, cname in no_price:
            print(f"  {name:12s} {cname or ''}")
    else:
        print("  （无，所有模块都有单价）")

    return 0


if __name__ == "__main__":
    sys.exit(main())

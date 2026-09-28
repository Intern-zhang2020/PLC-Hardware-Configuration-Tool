from __future__ import annotations

import heapq
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mysql.connector

from rack_builder import RackError, build_rack

CONFIG_PATH = Path(__file__).with_name("db_config.json")
HISTORY_TABLE = "selection_history"
HISTORY_MODULE_TABLE = "selection_history_module"
HISTORY_VIEW = "selection_history_overview"
HISTORY_MODULE_VIEW = "selection_history_module_overview"
DISTANCE_WEIGHTS = {"ai": 1.0, "ao": 1.0, "di": 1.0, "do": 1.0}

# 二维数组会一次性分配完整状态空间。
# 该上限用于防止 DI、DO 等需求过大时直接耗尽内存。
MAX_ARRAY_DP_STATES = 1_500_000
INF = 10**30


class SelectorError(Exception):
    """可以直接显示给用户的错误。"""


def _price_to_cents(value: Any) -> int:
    """
    价格统一换算为“分”，避免 947.14 在浮点运算中变成
    947.139999...，保证最低价格比较准确。
    """
    price = Decimal(str(value)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return int(price * 100)


def load_db_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise SelectorError("找不到 db_config.json。")

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    password = config.get("password")
    if password in (None, "", "请改成你的MySQL密码"):
        raise SelectorError("请先打开 db_config.json，填写你的 MySQL 密码。")

    return {
        "host": config.get("host", "localhost"),
        "user": config.get("user", "root"),
        "password": password,
        "database": config.get("database", "module_db"),
        "port": int(config.get("port", 3306)),
        "charset": "utf8mb4",
        "use_unicode": True,
    }


def load_modules() -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    从以下三张表读取模块：
        base
        analog_ext
        digital_ext

    有 IO 通道且价格有效的模块进入 DP 选型；
    MPU/CCU/电源/底板/DMM700 等系统模块没有 IO 通道，
    仍然保留在返回值中供机架组装（rack_builder）使用。

    AIO784 不依赖模块名称硬编码。
    只要数据库中：
        ai_number > 0
        ao_number > 0
        module_price 非空
    它就会自动进入 AI/AO 二维动态规划。
    """
    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor(dictionary=True)

    sql = """
    SELECT
        b.id,
        b.module_name,
        b.module_cname,
        b.module_type,
        b.module_price,
        b.power,

        COALESCE(a.ai_number, 0) AS ai_number,
        COALESCE(a.ao_number, 0) AS ao_number,

        COALESCE(d.di_number, 0) AS di_number,
        COALESCE(d.do_number, 0) AS do_number,
        COALESCE(d.dio_number, 0) AS dio_number

    FROM base AS b
    LEFT JOIN analog_ext AS a ON b.id = a.id
    LEFT JOIN digital_ext AS d ON b.id = d.id
    ORDER BY b.id
    """

    try:
        cursor.execute("SET NAMES utf8mb4")
        cursor.execute(sql)
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    modules: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for row in rows:
        ai = int(row["ai_number"] or 0)
        ao = int(row["ao_number"] or 0)
        di = int(row["di_number"] or 0)
        do = int(row["do_number"] or 0)
        dio = int(row["dio_number"] or 0)

        if row["module_price"] is None:
            warnings.append(f"{row['module_name']} 没有价格，已跳过。")
            continue

        price_cents = _price_to_cents(row["module_price"])
        if price_cents < 0:
            warnings.append(f"{row['module_name']} 的价格小于 0，已跳过。")
            continue

        modules.append(
            {
                "id": int(row["id"]),
                "module_name": row["module_name"] or "",
                "module_cname": row["module_cname"] or "",
                "module_type": row["module_type"] or "",
                "price_cents": price_cents,
                "power": float(row["power"] or 0),
                "ai": ai,
                "ao": ao,
                "di": di,
                "do": do,
                "dio": dio,
            }
        )

    aio784 = next(
        (module for module in modules
         if module["module_name"].strip().upper() == "AIO784"),
        None,
    )

    if aio784 is None:
        warnings.append(
            "没有读取到 AIO784。请检查 base.module_name、价格以及 "
            "analog_ext 中的 ai_number/ao_number。"
        )
    elif aio784["ai"] <= 0 or aio784["ao"] <= 0:
        warnings.append(
            f"AIO784 当前数据为 AI={aio784['ai']}、AO={aio784['ao']}，"
            "它必须同时大于 0 才能作为 AIO 模块发挥作用。"
        )

    return modules, warnings


def _check_separable_two_dimensional_model(
    modules: List[Dict[str, Any]],
) -> None:
    """
    当前总问题拆成：
        dp_analog[AI][AO]
        dp_digital[DI][DO]

    因此要求一个模块不能同时提供模拟量和数字量。
    AIO784 同时提供 AI/AO 没有问题，因为仍属于模拟量二维状态。
    """
    mixed_modules = []

    for module in modules:
        has_analog = module["ai"] > 0 or module["ao"] > 0
        has_digital = (
            module["di"] > 0
            or module["do"] > 0
            or module["dio"] > 0
        )

        if has_analog and has_digital:
            mixed_modules.append(module["module_name"])

    if mixed_modules:
        names = "、".join(mixed_modules)
        raise SelectorError(
            "发现同时提供模拟量和数字量的模块："
            f"{names}。当前两个二维动态规划会把这种模块重复购买，"
            "需要改成四维状态或专门的联合模型。"
        )


def _check_array_state_size(
    first_requirement: int,
    second_requirement: int,
    state_name: str,
) -> None:
    """
    二维数组会一次性创建：
        (first_requirement + 1) × (second_requirement + 1)
    个状态。

    当前求解器需要多张同样大小的二维数组，所以需求乘积过大时
    必须提前阻止，避免 Python 直接占满内存。
    """
    state_count = (
        (first_requirement + 1)
        * (second_requirement + 1)
    )

    if state_count > MAX_ARRAY_DP_STATES:
        raise SelectorError(
            f"{state_name} 二维数组需要 {state_count:,} 个状态，"
            f"超过当前安全上限 {MAX_ARRAY_DP_STATES:,}。"
            "二维数组会一次性分配全部状态，需求过大时容易耗尽内存。"
            "请降低单次需求规模、提高 MAX_ARRAY_DP_STATES（有内存风险），"
            "或改回稀疏字典/整数规划版本。"
        )


def _solve_analog(
    modules: List[Dict[str, Any]],
    req_ai: int,
    req_ao: int,
) -> Optional[Dict[str, Any]]:
    """
    模拟量二维数组动态规划（Dijkstra 状态扩展）：

        best_cost[ai][ao]
            到达 AI/AO 状态的最低价格。

        best_count[ai][ao]
            在最低价格相同时使用的最少模块数量。

        parent_ai[ai][ao]
        parent_ao[ai][ao]
        parent_module[ai][ao]
            用于从目标状态反向恢复模块组合。

    AIO784 会和 AIM、AOM 一起进入同一个 AI/AO 二维数组。
    """
    if req_ai == 0 and req_ao == 0:
        return {
            "price_cents": 0,
            "module_count": 0,
            "selected": [],
            "final_ai": 0,
            "final_ao": 0,
        }

    analog_modules = [
        module
        for module in modules
        if module["ai"] > 0 or module["ao"] > 0
    ]

    if not analog_modules:
        return None

    _check_array_state_size(
        req_ai,
        req_ao,
        "AI/AO",
    )

    row_count = req_ai + 1
    column_count = req_ao + 1

    # dp 最优价格二维数组。
    best_cost = [
        [INF] * column_count
        for _ in range(row_count)
    ]

    # 相同价格时，记录模块数量最少的方案。
    best_count = [
        [INF] * column_count
        for _ in range(row_count)
    ]

    # 父状态二维数组，用于恢复路径。
    parent_ai = [
        [-1] * column_count
        for _ in range(row_count)
    ]
    parent_ao = [
        [-1] * column_count
        for _ in range(row_count)
    ]
    parent_module = [
        [-1] * column_count
        for _ in range(row_count)
    ]

    best_cost[0][0] = 0
    best_count[0][0] = 0

    # 优先队列中的元素：
    # (累计价格, 模块数, AI状态, AO状态)
    queue: List[Tuple[int, int, int, int]] = [
        (0, 0, 0, 0)
    ]

    while queue:
        cost_cents, count, current_ai, current_ao = heapq.heappop(queue)

        # 队列里可能残留旧记录；只处理当前二维数组中的最优值。
        if (
            best_cost[current_ai][current_ao] != cost_cents
            or best_count[current_ai][current_ao] != count
        ):
            continue

        if current_ai == req_ai and current_ao == req_ao:
            break

        for module_index, module in enumerate(analog_modules):
            new_ai = min(
                req_ai,
                current_ai + module["ai"],
            )
            new_ao = min(
                req_ao,
                current_ao + module["ao"],
            )

            if new_ai == current_ai and new_ao == current_ao:
                continue

            new_cost = cost_cents + module["price_cents"]
            new_count = count + 1

            old_cost = best_cost[new_ai][new_ao]
            old_count = best_count[new_ai][new_ao]

            is_better = (
                new_cost < old_cost
                or (
                    new_cost == old_cost
                    and new_count < old_count
                )
            )

            if not is_better:
                continue

            best_cost[new_ai][new_ao] = new_cost
            best_count[new_ai][new_ao] = new_count

            parent_ai[new_ai][new_ao] = current_ai
            parent_ao[new_ai][new_ao] = current_ao
            parent_module[new_ai][new_ao] = module_index

            heapq.heappush(
                queue,
                (
                    new_cost,
                    new_count,
                    new_ai,
                    new_ao,
                ),
            )

    if best_cost[req_ai][req_ao] == INF:
        return None

    selected_indexes: List[int] = []
    current_ai = req_ai
    current_ao = req_ao

    while current_ai != 0 or current_ao != 0:
        module_index = parent_module[current_ai][current_ao]

        if module_index < 0:
            return None

        selected_indexes.append(module_index)

        previous_ai = parent_ai[current_ai][current_ao]
        previous_ao = parent_ao[current_ai][current_ao]

        current_ai = previous_ai
        current_ao = previous_ao

    selected_indexes.reverse()
    selected = [
        analog_modules[module_index]
        for module_index in selected_indexes
    ]

    return {
        "price_cents": best_cost[req_ai][req_ao],
        "module_count": best_count[req_ai][req_ao],
        "selected": selected,
        "final_ai": sum(module["ai"] for module in selected),
        "final_ao": sum(module["ao"] for module in selected),
    }

def _build_digital_options(
    modules: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    为每个数字量模块建立可选动作。

    对含 n 个 DIO 的一个模块，枚举：
        dio_to_di >= 0
        dio_to_do >= 0
        dio_to_di + dio_to_do <= n

    剩余部分：
        dio_spare = n - dio_to_di - dio_to_do

    例如 DIO732：
        固定 DI = 16
        固定 DO = 0
        DIO = 16

    某个动作可以是：
        DIO→DI = 0
        DIO→DO = 16
        贡献 DI = 16
        贡献 DO = 16
    """
    options: List[Dict[str, Any]] = []

    for module in modules:
        if (
            module["di"] == 0
            and module["do"] == 0
            and module["dio"] == 0
        ):
            continue

        dio = module["dio"]

        if dio == 0:
            options.append(
                {
                    "module": module,
                    "effective_di": module["di"],
                    "effective_do": module["do"],
                    "dio_to_di": 0,
                    "dio_to_do": 0,
                    "dio_spare": 0,
                }
            )
            continue

        for dio_to_di in range(dio + 1):
            max_do = dio - dio_to_di

            for dio_to_do in range(max_do + 1):
                dio_spare = (
                    dio
                    - dio_to_di
                    - dio_to_do
                )

                effective_di = (
                    module["di"]
                    + dio_to_di
                )
                effective_do = (
                    module["do"]
                    + dio_to_do
                )

                if effective_di == 0 and effective_do == 0:
                    continue

                options.append(
                    {
                        "module": module,
                        "effective_di": effective_di,
                        "effective_do": effective_do,
                        "dio_to_di": dio_to_di,
                        "dio_to_do": dio_to_do,
                        "dio_spare": dio_spare,
                    }
                )

    return options


def _solve_digital(
    modules: List[Dict[str, Any]],
    req_di: int,
    req_do: int,
) -> Optional[Dict[str, Any]]:
    """
    数字量二维数组动态规划：

        best_cost[di][do]
        best_count[di][do]

        parent_di[di][do]
        parent_do[di][do]
        parent_option[di][do]

    parent_option 保存的是“模块 + DIO 分配方式”的选项索引，
    因而最终仍可恢复 DIO→DI、DIO→DO 和备用 DIO。
    """
    if req_di == 0 and req_do == 0:
        return {
            "price_cents": 0,
            "module_count": 0,
            "selected_options": [],
            "final_di": 0,
            "final_do": 0,
            "raw_dio": 0,
            "dio_to_di": 0,
            "dio_to_do": 0,
            "dio_spare": 0,
        }

    options = _build_digital_options(modules)
    if not options:
        return None

    _check_array_state_size(
        req_di,
        req_do,
        "DI/DO",
    )

    row_count = req_di + 1
    column_count = req_do + 1

    best_cost = [
        [INF] * column_count
        for _ in range(row_count)
    ]
    best_count = [
        [INF] * column_count
        for _ in range(row_count)
    ]

    parent_di = [
        [-1] * column_count
        for _ in range(row_count)
    ]
    parent_do = [
        [-1] * column_count
        for _ in range(row_count)
    ]
    parent_option = [
        [-1] * column_count
        for _ in range(row_count)
    ]

    best_cost[0][0] = 0
    best_count[0][0] = 0

    queue: List[Tuple[int, int, int, int]] = [
        (0, 0, 0, 0)
    ]

    while queue:
        cost_cents, count, current_di, current_do = heapq.heappop(queue)

        if (
            best_cost[current_di][current_do] != cost_cents
            or best_count[current_di][current_do] != count
        ):
            continue

        if current_di == req_di and current_do == req_do:
            break

        for option_index, option in enumerate(options):
            new_di = min(
                req_di,
                current_di + option["effective_di"],
            )
            new_do = min(
                req_do,
                current_do + option["effective_do"],
            )

            if new_di == current_di and new_do == current_do:
                continue

            new_cost = (
                cost_cents
                + option["module"]["price_cents"]
            )
            new_count = count + 1

            old_cost = best_cost[new_di][new_do]
            old_count = best_count[new_di][new_do]

            is_better = (
                new_cost < old_cost
                or (
                    new_cost == old_cost
                    and new_count < old_count
                )
            )

            if not is_better:
                continue

            best_cost[new_di][new_do] = new_cost
            best_count[new_di][new_do] = new_count

            parent_di[new_di][new_do] = current_di
            parent_do[new_di][new_do] = current_do
            parent_option[new_di][new_do] = option_index

            heapq.heappush(
                queue,
                (
                    new_cost,
                    new_count,
                    new_di,
                    new_do,
                ),
            )

    if best_cost[req_di][req_do] == INF:
        return None

    selected_indexes: List[int] = []
    current_di = req_di
    current_do = req_do

    while current_di != 0 or current_do != 0:
        option_index = parent_option[current_di][current_do]

        if option_index < 0:
            return None

        selected_indexes.append(option_index)

        previous_di = parent_di[current_di][current_do]
        previous_do = parent_do[current_di][current_do]

        current_di = previous_di
        current_do = previous_do

    selected_indexes.reverse()
    selected_options = [
        options[option_index]
        for option_index in selected_indexes
    ]

    return {
        "price_cents": best_cost[req_di][req_do],
        "module_count": best_count[req_di][req_do],
        "selected_options": selected_options,
        "final_di": sum(
            option["effective_di"]
            for option in selected_options
        ),
        "final_do": sum(
            option["effective_do"]
            for option in selected_options
        ),
        "raw_dio": sum(
            option["module"]["dio"]
            for option in selected_options
        ),
        "dio_to_di": sum(
            option["dio_to_di"]
            for option in selected_options
        ),
        "dio_to_do": sum(
            option["dio_to_do"]
            for option in selected_options
        ),
        "dio_spare": sum(
            option["dio_spare"]
            for option in selected_options
        ),
    }

def _new_summary_row(
    module: Dict[str, Any],
    category: str,
) -> Dict[str, Any]:
    return {
        "id": module["id"],
        "category": category,
        "module_name": module["module_name"],
        "module_cname": module["module_cname"],
        "count": 0,
        "unit_price": module["price_cents"] / 100,
        "subtotal": 0.0,
        "ai": 0,
        "ao": 0,
        "fixed_di": 0,
        "fixed_do": 0,
        "raw_dio": 0,
        "dio_to_di": 0,
        "dio_to_do": 0,
        "dio_spare": 0,
        "effective_di": 0,
        "effective_do": 0,
    }


def _aggregate_result(
    analog_result: Dict[str, Any],
    digital_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    将逐个购买动作按模块型号汇总。
    """
    summary: Dict[int, Dict[str, Any]] = {}

    for module in analog_result["selected"]:
        module_id = module["id"]

        if module["ai"] > 0 and module["ao"] > 0:
            category = "模拟量-AIO"
        elif module["ai"] > 0:
            category = "模拟量-AI"
        else:
            category = "模拟量-AO"

        if module_id not in summary:
            summary[module_id] = _new_summary_row(
                module,
                category,
            )

        row = summary[module_id]
        row["count"] += 1
        row["subtotal"] += module["price_cents"] / 100
        row["ai"] += module["ai"]
        row["ao"] += module["ao"]

    for option in digital_result["selected_options"]:
        module = option["module"]
        module_id = module["id"]

        if module_id not in summary:
            summary[module_id] = _new_summary_row(
                module,
                "数字量",
            )

        row = summary[module_id]
        row["count"] += 1
        row["subtotal"] += module["price_cents"] / 100
        row["fixed_di"] += module["di"]
        row["fixed_do"] += module["do"]
        row["raw_dio"] += module["dio"]
        row["dio_to_di"] += option["dio_to_di"]
        row["dio_to_do"] += option["dio_to_do"]
        row["dio_spare"] += option["dio_spare"]
        row["effective_di"] += option["effective_di"]
        row["effective_do"] += option["effective_do"]

    for row in summary.values():
        # 避免展示 947.139999 之类的结果。
        row["subtotal"] = round(row["subtotal"], 2)

    return sorted(
        summary.values(),
        key=lambda row: (
            row["category"],
            row["id"],
        ),
    )


def solve_from_modules(
    modules: List[Dict[str, Any]],
    req_ai: int,
    req_ao: int,
    req_di: int,
    req_do: int,
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    不连接数据库的求解入口，可用于单元测试。
    """
    requirements = [
        req_ai,
        req_ao,
        req_di,
        req_do,
    ]

    if any(
        not isinstance(value, int)
        for value in requirements
    ):
        raise SelectorError("AI、AO、DI、DO 需求必须是整数。")

    if any(value < 0 for value in requirements):
        raise SelectorError("AI、AO、DI、DO 需求不能为负数。")

    if not modules:
        raise SelectorError("没有可用模块。")

    _check_separable_two_dimensional_model(modules)

    analog_result = _solve_analog(
        modules,
        req_ai,
        req_ao,
    )
    if analog_result is None:
        raise SelectorError(
            "现有模拟量模块无法同时满足 AI 和 AO 需求。"
        )

    digital_result = _solve_digital(
        modules,
        req_di,
        req_do,
    )
    if digital_result is None:
        raise SelectorError(
            "现有数字量模块无法同时满足 DI 和 DO 需求。"
        )

    rows = _aggregate_result(
        analog_result,
        digital_result,
    )

    total_price_cents = (
        analog_result["price_cents"]
        + digital_result["price_cents"]
    )

    selected_aio784_count = sum(
        1
        for module in analog_result["selected"]
        if module["module_name"].strip().upper() == "AIO784"
    )

    return {
        "requirement": {
            "ai": req_ai,
            "ao": req_ao,
            "di": req_di,
            "do": req_do,
        },
        "actual": {
            "ai": analog_result["final_ai"],
            "ao": analog_result["final_ao"],
            "di": digital_result["final_di"],
            "do": digital_result["final_do"],
            "raw_dio": digital_result["raw_dio"],
            "dio_to_di": digital_result["dio_to_di"],
            "dio_to_do": digital_result["dio_to_do"],
            "dio_spare": digital_result["dio_spare"],
        },
        "analog_price": analog_result["price_cents"] / 100,
        "digital_price": digital_result["price_cents"] / 100,
        "total_price": total_price_cents / 100,
        "module_count": (
            analog_result["module_count"]
            + digital_result["module_count"]
        ),
        "selected_aio784_count": selected_aio784_count,
        "rows": rows,
        "warnings": warnings or [],
        "algorithm": (
            "二维数组动态规划：dp[AI][AO] + dp[DI][DO]；"
            "不使用字典保存 DP 状态；价格最低，价格相同模块数最少"
        ),
    }


def _open_connection():
    """建立一个新的 MySQL 连接。调用者负责关闭。"""
    return mysql.connector.connect(**load_db_config())


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        f"SHOW COLUMNS FROM `{table_name}` LIKE %s",
        (column_name,),
    )
    return cursor.fetchone() is not None


def _next_legacy_table_name(cursor) -> str:
    """
    为旧的 JSON 历史表寻找一个不会冲突的备份名称。
    """
    base_name = "selection_history_legacy"
    if not _table_exists(cursor, base_name):
        return base_name

    index = 2
    while _table_exists(cursor, f"{base_name}_{index}"):
        index += 1
    return f"{base_name}_{index}"


def _create_history_schema(cursor) -> None:
    """
    创建规范化历史表：

    selection_history：
        每条需求一行，只存需求、实际结果、价格和时间。

    selection_history_module：
        每个历史需求选中的每种模块一行。

    不再把完整 JSON 塞进主表。
    """
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS `{HISTORY_TABLE}` (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            requirement_name VARCHAR(200) NOT NULL,

            req_ai INT UNSIGNED NOT NULL,
            req_ao INT UNSIGNED NOT NULL,
            req_di INT UNSIGNED NOT NULL,
            req_do INT UNSIGNED NOT NULL,

            actual_ai INT UNSIGNED NOT NULL,
            actual_ao INT UNSIGNED NOT NULL,
            actual_di INT UNSIGNED NOT NULL,
            actual_do INT UNSIGNED NOT NULL,

            raw_dio INT UNSIGNED NOT NULL DEFAULT 0,
            dio_to_di INT UNSIGNED NOT NULL DEFAULT 0,
            dio_to_do INT UNSIGNED NOT NULL DEFAULT 0,
            dio_spare INT UNSIGNED NOT NULL DEFAULT 0,

            analog_price DECIMAL(14, 2) NOT NULL,
            digital_price DECIMAL(14, 2) NOT NULL,
            total_price DECIMAL(14, 2) NOT NULL,
            module_count INT UNSIGNED NOT NULL,

            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (id),
            INDEX idx_requirement_name (requirement_name),
            INDEX idx_requirement_vector (req_ai, req_ao, req_di, req_do),
            INDEX idx_created_at (created_at)
        ) ENGINE=InnoDB
          DEFAULT CHARSET=utf8mb4
          COLLATE=utf8mb4_unicode_ci
        """
    )

    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS `{HISTORY_MODULE_TABLE}` (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            history_id BIGINT UNSIGNED NOT NULL,
            module_id INT NOT NULL,

            category VARCHAR(50) NOT NULL,
            module_name VARCHAR(100) NOT NULL,
            module_cname VARCHAR(200) NOT NULL,
            quantity INT UNSIGNED NOT NULL,

            unit_price DECIMAL(14, 2) NOT NULL,
            subtotal DECIMAL(14, 2) NOT NULL,

            ai_count INT UNSIGNED NOT NULL DEFAULT 0,
            ao_count INT UNSIGNED NOT NULL DEFAULT 0,
            fixed_di INT UNSIGNED NOT NULL DEFAULT 0,
            fixed_do INT UNSIGNED NOT NULL DEFAULT 0,
            raw_dio INT UNSIGNED NOT NULL DEFAULT 0,
            dio_to_di INT UNSIGNED NOT NULL DEFAULT 0,
            dio_to_do INT UNSIGNED NOT NULL DEFAULT 0,
            dio_spare INT UNSIGNED NOT NULL DEFAULT 0,
            effective_di INT UNSIGNED NOT NULL DEFAULT 0,
            effective_do INT UNSIGNED NOT NULL DEFAULT 0,

            PRIMARY KEY (id),
            INDEX idx_history_id (history_id),
            INDEX idx_module_name (module_name),

            CONSTRAINT fk_selection_history_module
                FOREIGN KEY (history_id)
                REFERENCES `{HISTORY_TABLE}` (id)
                ON DELETE CASCADE
                ON UPDATE CASCADE
        ) ENGINE=InnoDB
          DEFAULT CHARSET=utf8mb4
          COLLATE=utf8mb4_unicode_ci
        """
    )


def _create_history_views(cursor) -> None:
    """
    创建两个便于在 MySQL 命令行中查看的视图。
    """
    cursor.execute(
        f"""
        CREATE OR REPLACE VIEW `{HISTORY_VIEW}` AS
        SELECT
            id,
            requirement_name AS name,
            req_ai,
            req_ao,
            req_di,
            req_do,
            actual_ai,
            actual_ao,
            actual_di,
            actual_do,
            total_price,
            module_count,
            created_at
        FROM `{HISTORY_TABLE}`
        """
    )

    cursor.execute(
        f"""
        CREATE OR REPLACE VIEW `{HISTORY_MODULE_VIEW}` AS
        SELECT
            h.id AS history_id,
            h.requirement_name AS requirement_name,
            m.category,
            m.module_name,
            m.module_cname,
            m.quantity,
            m.unit_price,
            m.subtotal,
            m.ai_count,
            m.ao_count,
            m.fixed_di,
            m.fixed_do,
            m.raw_dio,
            m.dio_to_di,
            m.dio_to_do,
            m.dio_spare,
            m.effective_di,
            m.effective_do
        FROM `{HISTORY_TABLE}` AS h
        INNER JOIN `{HISTORY_MODULE_TABLE}` AS m
            ON h.id = m.history_id
        """
    )


def _insert_history_module_rows(
    cursor,
    history_id: int,
    rows: List[Dict[str, Any]],
) -> None:
    """
    把一个历史方案中的模块明细逐行写入明细表。
    """
    if not rows:
        return

    sql = f"""
    INSERT INTO `{HISTORY_MODULE_TABLE}` (
        history_id,
        module_id,
        category,
        module_name,
        module_cname,
        quantity,
        unit_price,
        subtotal,
        ai_count,
        ao_count,
        fixed_di,
        fixed_do,
        raw_dio,
        dio_to_di,
        dio_to_do,
        dio_spare,
        effective_di,
        effective_do
    ) VALUES (
        %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s
    )
    """

    values = []
    for row in rows:
        values.append(
            (
                history_id,
                int(row.get("id", 0)),
                str(row.get("category", "")),
                str(row.get("module_name", "")),
                str(row.get("module_cname", "")),
                int(row.get("count", 0)),
                round(float(row.get("unit_price", 0)), 2),
                round(float(row.get("subtotal", 0)), 2),
                int(row.get("ai", 0)),
                int(row.get("ao", 0)),
                int(row.get("fixed_di", 0)),
                int(row.get("fixed_do", 0)),
                int(row.get("raw_dio", 0)),
                int(row.get("dio_to_di", 0)),
                int(row.get("dio_to_do", 0)),
                int(row.get("dio_spare", 0)),
                int(row.get("effective_di", 0)),
                int(row.get("effective_do", 0)),
            )
        )

    cursor.executemany(sql, values)


def _migrate_legacy_history(cursor, legacy_table: str) -> None:
    """
    把旧 selection_history 中的 result_json 拆分到两张规范化表。

    旧表不会删除，而是保留为 selection_history_legacy，
    便于用户确认迁移结果后自行删除。
    """
    cursor.execute(
        f"SELECT * FROM `{legacy_table}` ORDER BY id ASC"
    )
    legacy_rows = cursor.fetchall()

    summary_sql = f"""
    INSERT INTO `{HISTORY_TABLE}` (
        id,
        requirement_name,
        req_ai,
        req_ao,
        req_di,
        req_do,
        actual_ai,
        actual_ao,
        actual_di,
        actual_do,
        raw_dio,
        dio_to_di,
        dio_to_do,
        dio_spare,
        analog_price,
        digital_price,
        total_price,
        module_count,
        created_at
    ) VALUES (
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s, %s
    )
    """

    for old_row in legacy_rows:
        result_data: Dict[str, Any] = {}
        raw_json = old_row.get("result_json")

        if raw_json:
            try:
                result_data = json.loads(raw_json)
            except (TypeError, json.JSONDecodeError):
                result_data = {}

        actual_data = result_data.get("actual", {})
        module_rows = result_data.get("rows", [])

        cursor.execute(
            summary_sql,
            (
                int(old_row["id"]),
                old_row["requirement_name"],
                int(old_row["req_ai"]),
                int(old_row["req_ao"]),
                int(old_row["req_di"]),
                int(old_row["req_do"]),
                int(old_row["actual_ai"]),
                int(old_row["actual_ao"]),
                int(old_row["actual_di"]),
                int(old_row["actual_do"]),
                int(actual_data.get("raw_dio", 0)),
                int(actual_data.get("dio_to_di", 0)),
                int(actual_data.get("dio_to_do", 0)),
                int(actual_data.get("dio_spare", 0)),
                old_row["analog_price"],
                old_row["digital_price"],
                old_row["total_price"],
                int(old_row["module_count"]),
                old_row["created_at"],
            ),
        )

        _insert_history_module_rows(
            cursor,
            int(old_row["id"]),
            module_rows if isinstance(module_rows, list) else [],
        )


def ensure_history_tables() -> Optional[str]:
    """
    确保规范化历史表存在。

    若检测到旧版 selection_history 含有 result_json：
        1. 将旧表自动改名为 selection_history_legacy
        2. 创建新的 selection_history
        3. 创建 selection_history_module
        4. 自动迁移旧数据
        5. 保留旧表作为备份

    返回值：
        发生迁移时返回旧表备份名称，否则返回 None。
    """
    connection = _open_connection()
    cursor = connection.cursor(dictionary=True)
    legacy_table: Optional[str] = None

    try:
        if _table_exists(cursor, HISTORY_TABLE) and _column_exists(
            cursor,
            HISTORY_TABLE,
            "result_json",
        ):
            legacy_table = _next_legacy_table_name(cursor)
            cursor.execute(
                f"RENAME TABLE `{HISTORY_TABLE}` TO `{legacy_table}`"
            )

        _create_history_schema(cursor)

        if legacy_table is not None:
            _migrate_legacy_history(cursor, legacy_table)

        _create_history_views(cursor)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    return legacy_table


def calculate_requirement_distance(
    current: Dict[str, int],
    historical: Dict[str, int],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    将 AI、AO、DI、DO 看作四维向量并计算距离。

    原始欧氏距离：
        sqrt(ΔAI² + ΔAO² + ΔDI² + ΔDO²)

    排名使用“相对归一化欧氏距离”：
        sqrt(Σ w_k * (Δk / max(1, 当前k, 历史k))²)

    使用归一化距离的原因：DI/DO 往往比 AI/AO 大很多；
    若直接用原始距离，DI/DO 会几乎完全决定结果。
    """
    weights = weights or DISTANCE_WEIGHTS
    dimensions = ("ai", "ao", "di", "do")

    deltas: Dict[str, int] = {}
    raw_square_sum = 0.0
    normalized_square_sum = 0.0

    for dimension in dimensions:
        current_value = int(current[dimension])
        historical_value = int(historical[dimension])
        delta = current_value - historical_value
        deltas[dimension] = delta

        raw_square_sum += delta * delta

        scale = max(1, current_value, historical_value)
        relative_delta = delta / scale
        normalized_square_sum += (
            float(weights.get(dimension, 1.0))
            * relative_delta
            * relative_delta
        )

    raw_distance = math.sqrt(raw_square_sum)
    normalized_distance = math.sqrt(normalized_square_sum)

    weight_sum = sum(float(weights.get(d, 1.0)) for d in dimensions)
    maximum_distance = math.sqrt(weight_sum) if weight_sum > 0 else 1.0
    similarity_percent = max(
        0.0,
        (1.0 - normalized_distance / maximum_distance) * 100.0,
    )

    return {
        "delta": deltas,
        "raw_distance": raw_distance,
        "normalized_distance": normalized_distance,
        "similarity_percent": similarity_percent,
    }


def _module_summary_from_detail_rows(rows: List[Dict[str, Any]]) -> str:
    parts = []

    for row in rows:
        module_name = str(row.get("module_name", ""))
        quantity = int(row.get("quantity", 0))

        if module_name and quantity > 0:
            parts.append(f"{module_name}×{quantity}")

    return "；".join(parts)


def find_closest_history(
    req_ai: int,
    req_ao: int,
    req_di: int,
    req_do: int,
) -> Optional[Dict[str, Any]]:
    """
    在所有已保存记录中寻找与当前需求最接近的一条。

    排序规则：
        1. 归一化四维距离最小
        2. 原始欧氏距离最小
        3. 若仍相同，选择较新的记录
    """
    ensure_history_tables()

    sql = f"""
    SELECT
        id,
        requirement_name,
        req_ai,
        req_ao,
        req_di,
        req_do,
        actual_ai,
        actual_ao,
        actual_di,
        actual_do,
        total_price,
        module_count,
        created_at
    FROM `{HISTORY_TABLE}`
    ORDER BY id ASC
    """

    connection = _open_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql)
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    if not rows:
        return None

    current = {
        "ai": req_ai,
        "ao": req_ao,
        "di": req_di,
        "do": req_do,
    }

    closest_row = None
    closest_distance = None
    closest_score = None

    for row in rows:
        historical = {
            "ai": int(row["req_ai"]),
            "ao": int(row["req_ao"]),
            "di": int(row["req_di"]),
            "do": int(row["req_do"]),
        }
        distance = calculate_requirement_distance(current, historical)

        score = (
            distance["normalized_distance"],
            distance["raw_distance"],
            -int(row["id"]),
        )

        if closest_score is None or score < closest_score:
            closest_score = score
            closest_row = row
            closest_distance = distance

    assert closest_row is not None
    assert closest_distance is not None

    connection = _open_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            f"""
            SELECT module_name, quantity
            FROM `{HISTORY_MODULE_TABLE}`
            WHERE history_id = %s
            ORDER BY id ASC
            """,
            (int(closest_row["id"]),),
        )
        module_rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    return {
        "id": int(closest_row["id"]),
        "requirement_name": closest_row["requirement_name"],
        "requirement": {
            "ai": int(closest_row["req_ai"]),
            "ao": int(closest_row["req_ao"]),
            "di": int(closest_row["req_di"]),
            "do": int(closest_row["req_do"]),
        },
        "actual": {
            "ai": int(closest_row["actual_ai"]),
            "ao": int(closest_row["actual_ao"]),
            "di": int(closest_row["actual_di"]),
            "do": int(closest_row["actual_do"]),
        },
        "total_price": float(closest_row["total_price"]),
        "module_count": int(closest_row["module_count"]),
        "created_at": str(closest_row["created_at"]),
        "module_summary": _module_summary_from_detail_rows(module_rows),
        **closest_distance,
    }


def save_selection_history(
    requirement_name: str,
    result: Dict[str, Any],
) -> int:
    """
    把当前选型保存到两张规范化表。

    主表：
        一条历史需求一行。

    明细表：
        每个选中模块型号一行。
    """
    ensure_history_tables()

    requirement = result["requirement"]
    actual = result["actual"]

    summary_sql = f"""
    INSERT INTO `{HISTORY_TABLE}` (
        requirement_name,
        req_ai,
        req_ao,
        req_di,
        req_do,
        actual_ai,
        actual_ao,
        actual_di,
        actual_do,
        raw_dio,
        dio_to_di,
        dio_to_do,
        dio_spare,
        analog_price,
        digital_price,
        total_price,
        module_count
    ) VALUES (
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s
    )
    """

    summary_values = (
        requirement_name,
        requirement["ai"],
        requirement["ao"],
        requirement["di"],
        requirement["do"],
        actual["ai"],
        actual["ao"],
        actual["di"],
        actual["do"],
        actual.get("raw_dio", 0),
        actual.get("dio_to_di", 0),
        actual.get("dio_to_do", 0),
        actual.get("dio_spare", 0),
        result["analog_price"],
        result["digital_price"],
        result["total_price"],
        result["module_count"],
    )

    connection = _open_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(summary_sql, summary_values)
        history_id = int(cursor.lastrowid)

        _insert_history_module_rows(
            cursor,
            history_id,
            result.get("rows", []),
        )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    return history_id

def select_modules(
    req_ai: int,
    req_ao: int,
    req_di: int,
    req_do: int,
    main_ccu: str = "CCU702",
    ext_first_ccu: str = "CCU702",
    ext_last_ccu: str = "CCU702",
    ext_other_ccu: str = "CCU702",
) -> Dict[str, Any]:
    """
    只计算，不保存历史。测试程序可以继续使用这个入口。

    DP 求解完成后自动组装底板机架：
        MPU710 + CCU + 电源 + DMM700 填充 + BSM/BSE 底板。

    CCU 可按底板位置选择（CCU700/CCU701/CCU702，默认 CCU702）：
        main_ccu      主站底板；
        ext_first_ccu 第一块扩展底板（仅有一块扩展底板时也用它）；
        ext_last_ccu  最后一块扩展底板（扩展底板数 >= 2 时生效）；
        ext_other_ccu 其余扩展底板。
    """
    modules, warnings = load_modules()

    result = solve_from_modules(
        modules=modules,
        req_ai=req_ai,
        req_ao=req_ao,
        req_di=req_di,
        req_do=req_do,
        warnings=warnings,
    )

    try:
        build_rack(
            modules,
            result,
            main_ccu=main_ccu,
            ext_first_ccu=ext_first_ccu,
            ext_last_ccu=ext_last_ccu,
            ext_other_ccu=ext_other_ccu,
        )
    except RackError as exc:
        raise SelectorError(str(exc)) from exc

    return result


def select_modules_and_save(
    requirement_name: str,
    req_ai: int,
    req_ao: int,
    req_di: int,
    req_do: int,
    main_ccu: str = "CCU702",
    ext_first_ccu: str = "CCU702",
    ext_last_ccu: str = "CCU702",
    ext_other_ccu: str = "CCU702",
) -> Dict[str, Any]:
    """
    GUI 使用的完整入口：
        1. 计算最低价格方案
        2. 与已有历史需求比较
        3. 保存当前需求和结果
        4. 返回当前方案及最接近历史记录
    """
    clean_name = requirement_name.strip()
    if not clean_name:
        raise SelectorError("名称不能为空。")
    if len(clean_name) > 200:
        raise SelectorError("名称不能超过 200 个字符。")

    result = select_modules(
        req_ai=req_ai,
        req_ao=req_ao,
        req_di=req_di,
        req_do=req_do,
        main_ccu=main_ccu,
        ext_first_ccu=ext_first_ccu,
        ext_last_ccu=ext_last_ccu,
        ext_other_ccu=ext_other_ccu,
    )

    # 必须在保存当前记录之前查找，否则当前记录会以距离 0 匹配自己。
    closest_history = find_closest_history(
        req_ai=req_ai,
        req_ao=req_ao,
        req_di=req_di,
        req_do=req_do,
    )

    result["requirement_name"] = clean_name
    history_id = save_selection_history(clean_name, result)

    result["history_id"] = history_id
    result["closest_history"] = closest_history
    result["distance_method"] = (
        "AI/AO/DI/DO 四维相对归一化欧氏距离；"
        "距离越小越接近"
    )
    return result

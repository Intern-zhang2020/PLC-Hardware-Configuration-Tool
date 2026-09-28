"""
底板/机架组装规则。

选型流程在二维 DP 求解出 IO 模块组合之后，由本模块完成机架组装：

    1. 主站底板（BSM 开头，每次选型只能选一个）
       - 第 1-2 槽：MPU710（必选，占用 2 个槽位）
       - 第 3 槽：CCU 模块（默认 CCU702）
       - 之后：IO 模块按 DP 结果顺序装入
       - 空槽：DMM700 填充
       - 最后 1 槽：电源模块（默认 PWR750）
    2. 主站底板装不下时使用扩展底板（BSE 开头，可以多块）
       - 第 1 槽：CCU 模块（默认 CCU702，扩展底板不能放 MPU710）
       - 之后：剩余 IO 模块
       - 空槽：DMM700 填充
       - 最后 1 槽：电源模块（默认 PWR750）
    3. 底板型号按“能装下的最小槽位数”选择；
       主站底板从 BSM706（6 槽）到 BSM716（16 槽），
       扩展底板从 BSE705（5 槽）到 BSE714（14 槽）。

槽位占用规则：MPU710 和 DIM780 占 2 个槽位，其他模块占 1 个槽位。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 占用 2 个槽位的模块，其余默认占 1 个。
SLOT_OVERRIDE = {"MPU710": 2, "DIM780": 2}

DEFAULT_CCU_MODULE = "CCU702"
DEFAULT_PWR_MODULE = "PWR750"
DEFAULT_DMM_MODULE = "DMM700"
MPU_MODULE = "MPU710"

MAIN_BOARD_PREFIX = "BSM"
EXT_BOARD_PREFIX = "BSE"

MAIN_BOARD_SLOT_CHOICES = (6, 8, 10, 12, 14, 16)
EXT_BOARD_SLOT_CHOICES = (5, 6, 7, 9, 11, 14)

# 主站底板固定占用：MPU710(2) + CCU(1) + 电源(1)。
MAIN_RESERVED_SLOTS = 4
# 扩展底板固定占用：CCU(1) + 电源(1)。
EXT_RESERVED_SLOTS = 2

BOARD_CATEGORY_MAIN = "底板-主站"
BOARD_CATEGORY_EXT = "底板-扩展"
CATEGORY_MPU = "主控"
CATEGORY_CCU = "CCU"
CATEGORY_PWR = "电源"
CATEGORY_DMM = "填充"


class RackError(Exception):
    """可以直接显示给用户的机架组装错误。"""


def module_slot_count(module_name: str) -> int:
    """返回模块占用的槽位数。"""
    return SLOT_OVERRIDE.get(module_name.strip().upper(), 1)


def parse_board_slots(module_name: str) -> int:
    """从底板名称解析槽位数（取最后两位数字），例如 BSM706 -> 6。"""
    match = re.search(r"(\d{2})$", module_name.strip())
    return int(match.group(1)) if match else 0


def _module_by_name(modules: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        module["module_name"].strip().upper(): module
        for module in modules
    }


def _pick_board(
    boards: Dict[int, Dict[str, Any]],
    slot_choices: Tuple[int, ...],
    needed_slots: int,
    reserved_slots: int,
) -> Optional[int]:
    """选择能装下 needed_slots 的最小底板槽位数；装不下返回 None。"""
    for slots in slot_choices:
        if slots in boards and slots - reserved_slots >= needed_slots:
            return slots
    return None


def _first_fit(
    items: List[Tuple[Dict[str, Any], int]],
    capacity: int,
) -> Tuple[List[Tuple[Dict[str, Any], int]], List[Tuple[Dict[str, Any], int]]]:
    """
    把 (模块行, 槽位数) 序列按顺序装入容量为 capacity 的底板。

    装不下的模块跳过并留给下一块底板，返回 (已装入, 剩余)。
    """
    placed: List[Tuple[Dict[str, Any], int]] = []
    remaining: List[Tuple[Dict[str, Any], int]] = []
    used = 0

    for item in items:
        slots = item[1]
        if used + slots <= capacity:
            placed.append(item)
            used += slots
        else:
            remaining.append(item)

    return placed, remaining


def _new_board_row(
    module: Dict[str, Any],
    board_index: int,
    slots: int,
    is_main: bool,
) -> Dict[str, Any]:
    price = module["price_cents"] / 100
    return {
        "id": module["id"],
        "board": board_index,
        "category": BOARD_CATEGORY_MAIN if is_main else BOARD_CATEGORY_EXT,
        "module_name": module["module_name"],
        "module_cname": module["module_cname"],
        "count": 1,
        "slots": slots,
        "unit_price": price,
        "subtotal": price,
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


def _new_module_row(
    module: Dict[str, Any],
    board_index: int,
    category: str,
    count: int,
    slots: int,
    io_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    price = module["price_cents"] / 100
    row = {
        "id": module["id"],
        "board": board_index,
        "category": category,
        "module_name": module["module_name"],
        "module_cname": module["module_cname"],
        "count": count,
        "slots": slots,
        "unit_price": price,
        "subtotal": round(price * count, 2),
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

    if io_row is not None:
        # io_row 的贡献字段是“该型号全部实例的总和”，
        # 折算回单实例再乘本底板上的数量。
        old_count = max(1, int(io_row.get("count", 1)))
        for key in (
            "ai",
            "ao",
            "fixed_di",
            "fixed_do",
            "raw_dio",
            "dio_to_di",
            "dio_to_do",
            "dio_spare",
            "effective_di",
            "effective_do",
        ):
            per_instance = int(io_row.get(key, 0)) // old_count
            row[key] = per_instance * count

    return row


def _layout_text(sequence: List[Tuple[str, int]], board_slots: int) -> str:
    """把模块序列转成“槽1-2:MPU710，槽3:CCU702 …”的紧凑描述。"""
    parts: List[str] = []
    slot = 1

    for name, slots in sequence:
        end = slot + slots - 1
        if slots > 1:
            parts.append(f"槽{slot}-{end}:{name}")
        else:
            parts.append(f"槽{slot}:{name}")
        slot = end + 1

    return "，".join(parts)


def build_rack(
    modules: List[Dict[str, Any]],
    result: Dict[str, Any],
    main_ccu: str = DEFAULT_CCU_MODULE,
    ext_first_ccu: str = DEFAULT_CCU_MODULE,
    ext_last_ccu: str = DEFAULT_CCU_MODULE,
    ext_other_ccu: str = DEFAULT_CCU_MODULE,
) -> Dict[str, Any]:
    """
    根据 DP 选型结果组装底板，重构 result 中的 rows，
    更新 total_price / module_count，并写入 result["rack"]。

    CCU 选择：
        main_ccu      主站底板使用的 CCU；
        ext_first_ccu 第一块扩展底板使用的 CCU
                      （只有一块扩展底板时也用它）；
        ext_last_ccu  最后一块扩展底板使用的 CCU
                      （仅当扩展底板数 >= 2 时生效）；
        ext_other_ccu 其余扩展底板使用的 CCU。

    返回机架摘要字典。
    """
    by_name = _module_by_name(modules)

    def get_module(name: str) -> Dict[str, Any]:
        module = by_name.get(name.strip().upper())
        if module is None:
            raise RackError(
                f"数据库 base 表中找不到 {name}，无法组装底板。"
            )
        return module

    ccu_choices = {
        "main": main_ccu.strip().upper(),
        "first": ext_first_ccu.strip().upper(),
        "last": ext_last_ccu.strip().upper(),
        "other": ext_other_ccu.strip().upper(),
    }
    ccu_modules = {key: get_module(name) for key, name in ccu_choices.items()}

    mpu = get_module(MPU_MODULE)
    pwr = get_module(DEFAULT_PWR_MODULE)
    dmm = get_module(DEFAULT_DMM_MODULE)

    main_boards: Dict[int, Dict[str, Any]] = {}
    ext_boards: Dict[int, Dict[str, Any]] = {}

    for module in modules:
        name = module["module_name"].strip().upper()
        if name.startswith(MAIN_BOARD_PREFIX):
            slots = parse_board_slots(name)
            if slots in MAIN_BOARD_SLOT_CHOICES:
                main_boards[slots] = module
        elif name.startswith(EXT_BOARD_PREFIX):
            slots = parse_board_slots(name)
            if slots in EXT_BOARD_SLOT_CHOICES:
                ext_boards[slots] = module

    if not main_boards:
        raise RackError(
            "数据库中没有可用的 BSM 主站底板"
            f"（{MAIN_BOARD_PREFIX}706~{MAIN_BOARD_PREFIX}716）。"
        )

    # ------------------------------------------------------------------
    # IO 模块实例队列（保持 DP 汇总顺序：类别、id）
    # ------------------------------------------------------------------
    io_items: List[Tuple[Dict[str, Any], int]] = []
    for row in result.get("rows", []):
        slots = module_slot_count(row["module_name"])
        for _ in range(int(row["count"])):
            io_items.append((row, slots))

    io_slots_needed = sum(slots for _, slots in io_items)

    # ------------------------------------------------------------------
    # 主站底板
    # ------------------------------------------------------------------
    main_slots = _pick_board(
        main_boards,
        MAIN_BOARD_SLOT_CHOICES,
        io_slots_needed,
        MAIN_RESERVED_SLOTS,
    )
    if main_slots is None:
        main_slots = MAIN_BOARD_SLOT_CHOICES[-1]

    main_capacity = main_slots - MAIN_RESERVED_SLOTS
    placed_main, remaining = _first_fit(io_items, main_capacity)

    boards: List[Dict[str, Any]] = [
        {
            "module": main_boards[main_slots],
            "slots": main_slots,
            "is_main": True,
            "placed": placed_main,
        }
    ]

    # ------------------------------------------------------------------
    # 扩展底板（主站底板装不下时）
    # ------------------------------------------------------------------
    while remaining:
        needed = sum(slots for _, slots in remaining)

        ext_slots = None
        for slots in EXT_BOARD_SLOT_CHOICES:
            if slots in ext_boards and slots - EXT_RESERVED_SLOTS >= needed:
                ext_slots = slots
                break

        if ext_slots is None:
            if not ext_boards:
                raise RackError(
                    "所需槽位超出主站底板容量，且数据库中没有可用的 "
                    "BSE 扩展底板。"
                )
            ext_slots = EXT_BOARD_SLOT_CHOICES[-1]
            if ext_slots not in ext_boards:
                ext_slots = max(ext_boards)

        capacity = ext_slots - EXT_RESERVED_SLOTS
        placed, remaining = _first_fit(remaining, capacity)

        if not placed:
            raise RackError(
                "扩展底板槽位不足，无法装入剩余模块。"
            )

        boards.append(
            {
                "module": ext_boards[ext_slots],
                "slots": ext_slots,
                "is_main": False,
                "placed": placed,
            }
        )

    # ------------------------------------------------------------------
    # 组装每块底板的模块序列并重构 rows
    # ------------------------------------------------------------------
    new_rows: List[Dict[str, Any]] = []
    board_infos: List[Dict[str, Any]] = []

    dmm_total = 0
    ccu_counts: Dict[str, int] = {}
    pwr_count = 0
    system_price_cents = 0

    ext_board_count = len(boards) - 1

    for board_index, board in enumerate(boards, start=1):
        board_module = board["module"]
        board_slots = board["slots"]

        if board["is_main"]:
            ccu = ccu_modules["main"]
        else:
            ext_index = board_index - 2  # 0-based 扩展底板序号
            if ext_index == 0:
                ccu = ccu_modules["first"]
            elif ext_index == ext_board_count - 1:
                ccu = ccu_modules["last"]
            else:
                ccu = ccu_modules["other"]
        ccu_counts[ccu["module_name"]] = (
            ccu_counts.get(ccu["module_name"], 0) + 1
        )

        # 模块序列：[(名称, 槽位, 模块记录, io_row)]
        sequence: List[Tuple[str, int, Dict[str, Any], Optional[Dict[str, Any]]]] = []

        if board["is_main"]:
            sequence.append((mpu["module_name"], 2, mpu, None))

        sequence.append((ccu["module_name"], 1, ccu, None))
        system_price_cents += ccu["price_cents"]

        # sequence 目前只包含 MPU（若有）和 CCU，电源模块最后追加。
        used = sum(slots for _, slots, _, _ in sequence)

        for io_row, slots in board["placed"]:
            io_module = by_name.get(io_row["module_name"].strip().upper())
            if io_module is None:
                raise RackError(
                    f"找不到模块 {io_row['module_name']} 的数据库记录。"
                )
            sequence.append(
                (io_module["module_name"], slots, io_module, io_row)
            )
            used += slots

        fill_count = board_slots - used - 1  # 最后 1 槽留给电源
        if fill_count < 0:
            raise RackError(
                f"底板 {board_module['module_name']} 槽位溢出，"
                "请检查槽位占用规则。"
            )

        for _ in range(fill_count):
            sequence.append((dmm["module_name"], 1, dmm, None))
        dmm_total += fill_count

        sequence.append((pwr["module_name"], 1, pwr, None))
        system_price_cents += pwr["price_cents"]
        pwr_count += 1

        # 底板行
        new_rows.append(
            _new_board_row(board_module, board_index, board_slots, board["is_main"])
        )
        system_price_cents += board_module["price_cents"]

        # 模块行（相邻同型号合并）
        contribution_keys = (
            "ai",
            "ao",
            "fixed_di",
            "fixed_do",
            "raw_dio",
            "dio_to_di",
            "dio_to_do",
            "dio_spare",
            "effective_di",
            "effective_do",
        )

        merged: List[Dict[str, Any]] = []
        for name, slots, module, io_row in sequence:
            if merged and merged[-1]["module_name"] == name:
                target = merged[-1]
                target["count"] += 1
                target["slots"] += slots
                target["subtotal"] = round(
                    target["unit_price"] * target["count"], 2
                )
                if io_row is not None:
                    old_count = max(1, int(io_row["count"]))
                    for key in contribution_keys:
                        per = int(io_row.get(key, 0)) // old_count
                        target[key] += per
            else:
                category = CATEGORY_MPU if name == mpu["module_name"] else None
                if category is None and io_row is not None:
                    category = str(io_row.get("category", "数字量"))
                elif category is None:
                    category = {
                        ccu["module_name"]: CATEGORY_CCU,
                        pwr["module_name"]: CATEGORY_PWR,
                        dmm["module_name"]: CATEGORY_DMM,
                    }.get(name, "其他")

                merged.append(
                    _new_module_row(
                        module,
                        board_index,
                        category,
                        1,
                        slots,
                        io_row,
                    )
                )

        new_rows.extend(merged)

        layout_items = [(name, slots) for name, slots, _, _ in sequence]
        board_infos.append(
            {
                "board_index": board_index,
                "module_name": board_module["module_name"],
                "module_cname": board_module["module_cname"],
                "slots": board_slots,
                "is_main": board["is_main"],
                "used_slots": sum(slots for _, slots in layout_items),
                "ccu_module": ccu["module_name"],
                "dmm_count": fill_count,
                "io_module_count": len(board["placed"]),
                "layout": _layout_text(layout_items, board_slots),
            }
        )

    # ------------------------------------------------------------------
    # 更新 result
    # ------------------------------------------------------------------
    io_price_cents = int(round(result["total_price"] * 100))
    new_total_cents = io_price_cents + system_price_cents

    io_module_count = result["module_count"]
    new_module_count = (
        io_module_count
        + 1  # MPU710
        + sum(ccu_counts.values())
        + pwr_count
        + dmm_total
    )

    result["rows"] = new_rows
    result["total_price"] = new_total_cents / 100
    result["module_count"] = new_module_count
    result["rack"] = {
        "boards": board_infos,
        "board_count": len(boards),
        "main_board": board_infos[0]["module_name"] if board_infos else None,
        "main_ccu": ccu_choices["main"],
        "ext_first_ccu": ccu_choices["first"],
        "ext_last_ccu": ccu_choices["last"],
        "ext_other_ccu": ccu_choices["other"],
        "ccu_counts": ccu_counts,
        "ccu_total": sum(ccu_counts.values()),
        "pwr_module": pwr["module_name"],
        "dmm_module": dmm["module_name"],
        "pwr_count": pwr_count,
        "dmm_total": dmm_total,
        "io_module_count": io_module_count,
        "system_price": system_price_cents / 100,
        "slot_rule": (
            "MPU710、DIM780 占 2 个槽位，其他模块占 1 个槽位；"
            "主站底板 BSM 仅一块（MPU710 必选且占用第 1-2 槽）；"
            "每块底板第 1 槽为 CCU（扩展底板），末槽为电源，"
            "空槽用 DMM700 填充"
        ),
    }

    return result["rack"]

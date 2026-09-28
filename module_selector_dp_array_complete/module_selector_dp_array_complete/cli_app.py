from __future__ import annotations

import argparse
import time

from cad_layout import generate_combined_cad
from selector_core import SelectorError, select_modules_and_save


def read_nonnegative_int(prompt: str) -> int:
    while True:
        try:
            value = int(input(prompt).strip())
        except ValueError:
            print("请输入整数。")
            continue

        if value < 0:
            print("不能输入负数。")
            continue

        return value


def read_name(prompt: str) -> str:
    while True:
        name = input(prompt).strip()
        if not name:
            print("名称不能为空。")
            continue
        if len(name) > 200:
            print("名称不能超过 200 个字符。")
            continue
        return name


def main():
    parser = argparse.ArgumentParser(
        description="二维数组动态规划 I/O 模块最低价格选型（含历史比较与 CAD 输出）",
    )
    parser.add_argument(
        "--no-cad",
        action="store_true",
        help="不生成模块组合 CAD 图",
    )
    parser.add_argument(
        "--main-ccu",
        choices=("CCU700", "CCU701", "CCU702"),
        default="CCU702",
        help="主站底板使用的 CCU 模块（默认 CCU702）",
    )
    parser.add_argument(
        "--ext-first-ccu",
        choices=("CCU700", "CCU701", "CCU702"),
        default="CCU702",
        help="第一块扩展底板使用的 CCU 模块（默认 CCU702）",
    )
    parser.add_argument(
        "--ext-last-ccu",
        choices=("CCU700", "CCU701", "CCU702"),
        default="CCU702",
        help="最后一块扩展底板使用的 CCU 模块（默认 CCU702）",
    )
    parser.add_argument(
        "--ext-other-ccu",
        choices=("CCU700", "CCU701", "CCU702"),
        default="CCU702",
        help="其余扩展底板使用的 CCU 模块（默认 CCU702）",
    )
    args = parser.parse_args()

    print("二维数组动态规划 I/O 模块最低价格选型（含历史比较与 CAD 输出）")
    print("=" * 68)

    requirement_name = read_name("需求名称：")
    req_ai = read_nonnegative_int("AI 需求：")
    req_ao = read_nonnegative_int("AO 需求：")
    req_di = read_nonnegative_int("DI 需求：")
    req_do = read_nonnegative_int("DO 需求：")

    started = time.perf_counter()

    try:
        result = select_modules_and_save(
            requirement_name=requirement_name,
            req_ai=req_ai,
            req_ao=req_ao,
            req_di=req_di,
            req_do=req_do,
            main_ccu=args.main_ccu,
            ext_first_ccu=args.ext_first_ccu,
            ext_last_ccu=args.ext_last_ccu,
            ext_other_ccu=args.ext_other_ccu,
        )
    except SelectorError as exc:
        print(f"\n选型失败：{exc}")
        return

    elapsed = time.perf_counter() - started

    print("\n推荐方案")
    print("=" * 90)

    for row in result["rows"]:
        board_tag = f"底板{row['board']}" if row.get("board") else "-"
        print(
            f"{row['category']} | "
            f"{row['module_name']} × {row['count']} | "
            f"{board_tag} {row.get('slots', 0)}槽 | "
            f"单价 {row['unit_price']:.2f} | "
            f"小计 {row['subtotal']:.2f}"
        )
        if str(row["category"]).startswith("底板"):
            continue
        print(
            f"  AI={row['ai']}，AO={row['ao']}，"
            f"固定DI={row['fixed_di']}，"
            f"固定DO={row['fixed_do']}，"
            f"DIO→DI={row['dio_to_di']}，"
            f"DIO→DO={row['dio_to_do']}，"
            f"备用DIO={row['dio_spare']}"
        )

    rack = result.get("rack")
    if rack:
        print("\n机架组装")
        print("-" * 90)
        for board in rack["boards"]:
            kind = "主站" if board["is_main"] else "扩展"
            print(
                f"底板{board['board_index']}（{kind}）："
                f"{board['module_name']}，{board['slots']} 槽位，"
                f"CCU={board.get('ccu_module', '')}，"
                f"含 IO 模块 {board['io_module_count']} 个，"
                f"DMM700 填充 {board['dmm_count']} 个"
            )
            print(f"  {board['layout']}")
        ccu_text = "，".join(
            f"{name} ×{count}"
            for name, count in rack.get("ccu_counts", {}).items()
        )
        print(
            f"MPU710 ×1，CCU：{ccu_text}，"
            f"{rack['pwr_module']} ×{rack['pwr_count']}，"
            f"{rack['dmm_module']} ×{rack['dmm_total']}，"
            f"底板共 {rack['board_count']} 块"
        )

    actual = result["actual"]

    print("-" * 90)
    print(f"名称：{result['requirement_name']}")
    print(f"历史记录 ID：{result['history_id']}")
    print(
        f"实际满足：AI={actual['ai']}，"
        f"AO={actual['ao']}，"
        f"DI={actual['di']}，"
        f"DO={actual['do']}"
    )
    print(f"AIO784 选中数量：{result['selected_aio784_count']}")
    print(f"总价格：{result['total_price']:.2f}")
    print(f"模块总数：{result['module_count']}")

    closest = result.get("closest_history")
    if closest is None:
        print("历史比较：这是第一条历史记录。")
    else:
        old = closest["requirement"]
        delta = closest["delta"]
        print(
            "最接近历史需求："
            f"{closest['requirement_name']}，"
            f"AI={old['ai']}，AO={old['ao']}，"
            f"DI={old['di']}，DO={old['do']}"
        )
        print(
            f"差值：ΔAI={delta['ai']:+d}，ΔAO={delta['ao']:+d}，"
            f"ΔDI={delta['di']:+d}，ΔDO={delta['do']:+d}"
        )
        print(
            f"归一化距离：{closest['normalized_distance']:.6f}，"
            f"参考相似度：{closest['similarity_percent']:.2f}%"
        )

    print(f"求解用时：{elapsed:.3f} 秒")

    if not args.no_cad:
        print("\n正在生成模块组合 CAD 图……")
        cad_result = generate_combined_cad(
            result,
            requirement_name,
            progress=print,
        )

        if cad_result["success"]:
            print("-" * 90)
            print(f"CAD 组合图文件夹：{cad_result['folder']}")
            if cad_result["dwg_path"]:
                print(f"DWG：{cad_result['dwg_path']}")
            if cad_result["dxf_path"]:
                print(f"DXF：{cad_result['dxf_path']}")
            print(f"图中绘制模块数：{cad_result['placed_count']}")
        else:
            print(f"CAD 组合图生成失败：{cad_result['error']}")

        for warning in cad_result["warnings"]:
            print(f"CAD提示：{warning}")


if __name__ == "__main__":
    main()

import sys

sys.path.insert(0, r"D:\PLC组态计算\module_selector_dp_array_complete\module_selector_dp_array_complete")

from selector_core import select_modules


def show(title, req):
    print(f"\n{'=' * 70}\n{title}: AI={req[0]} AO={req[1]} DI={req[2]} DO={req[3]}\n{'=' * 70}")
    result = select_modules(req_ai=req[0], req_ao=req[1], req_di=req[2], req_do=req[3])
    rack = result["rack"]

    for row in result["rows"]:
        print(
            f"  底板{row['board']} | {row['category']:<10} | {row['module_name']:<8} x{row['count']:<3}"
            f"槽位={row['slots']:<3} 单价={row['unit_price']:>8.2f} 小计={row['subtotal']:>9.2f}"
        )

    print(f"\n  汇总: IO价格={result['analog_price'] + result['digital_price']:.2f}"
          f" 系统价格={rack['system_price']:.2f}"
          f" 总价={result['total_price']:.2f}")
    print(f"  模块数(不含底板)={result['module_count']}"
          f" = IO{rack['io_module_count']} + MPU1 + CCU{rack['ccu_count']}"
          f" + PWR{rack['pwr_count']} + DMM{rack['dmm_total']}")
    print(f"  底板数={rack['board_count']}")

    # 槽位校验
    for b in rack["boards"]:
        print(f"  底板{b['board_index']}: {b['module_name']}({b['slots']}槽)"
              f" 已用{b['used_slots']} DMM{b['dmm_count']}")
        print(f"    {b['layout']}")


show("全零需求", (0, 0, 0, 0))
show("小需求", (10, 8, 100, 40))
show("中需求", (40, 20, 1000, 500))
show("大需求-多扩展底板", (200, 100, 5000, 3000))

import sys

sys.path.insert(0, r"D:\PLC组态计算\module_selector_dp_array_complete\module_selector_dp_array_complete")

from selector_core import load_modules

modules, warnings = load_modules()
print("warnings:", warnings)
print("total modules:", len(modules))
for m in modules:
    name = m["module_name"].strip().upper()
    if name.startswith(("BSM", "BSE", "BRE", "BRM", "MPU", "CCU", "PWR", "DMM")):
        print(name, "price_cents=", m["price_cents"], "ai=", m["ai"], "di=", m["di"])

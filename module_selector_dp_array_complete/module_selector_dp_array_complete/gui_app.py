from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from cad_layout import generate_combined_cad
from selector_core import SelectorError, select_modules_and_save

CCU_CHOICES = ("CCU700", "CCU701", "CCU702")
DEFAULT_CCU = "CCU702"


class ModuleSelectorGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(
            "二维数组动态规划 I/O 模块选型（机架组装、CAD 输出与历史比较）"
        )
        self.root.geometry("1320x880")
        self.root.minsize(1080, 620)

        self.name_var = tk.StringVar(value="")
        self.variables = {
            "AI": tk.StringVar(value="0"),
            "AO": tk.StringVar(value="0"),
            "DI": tk.StringVar(value="0"),
            "DO": tk.StringVar(value="0"),
        }
        self.cad_var = tk.BooleanVar(value=True)
        self.ccu_vars = {
            "main": tk.StringVar(value=DEFAULT_CCU),
            "ext_first": tk.StringVar(value=DEFAULT_CCU),
            "ext_last": tk.StringVar(value=DEFAULT_CCU),
            "ext_other": tk.StringVar(value=DEFAULT_CCU),
        }

        self.status_var = tk.StringVar(value="就绪")
        self.start_time = 0.0

        self._create_widgets()

    def _create_widgets(self):
        # ============================================================
        # 1. 用户需求区域
        # ============================================================
        input_frame = ttk.LabelFrame(
            self.root,
            text="用户需求",
        )
        input_frame.pack(
            fill="x",
            padx=12,
            pady=10,
        )
        input_frame.columnconfigure(0, weight=1)

        # ============================================================
        # 1.1 名称输入行
        # ============================================================
        name_frame = ttk.Frame(input_frame)
        name_frame.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=12,
            pady=(12, 6),
        )
        name_frame.columnconfigure(1, weight=1)

        ttk.Label(
            name_frame,
            text="名称：",
        ).grid(
            row=0,
            column=0,
            padx=(0, 6),
            sticky="e",
        )

        self.name_entry = ttk.Entry(
            name_frame,
            textvariable=self.name_var,
            width=40,
        )
        self.name_entry.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 24),
        )

        # ============================================================
        # 1.2 AI、AO、DI、DO 等间距输入行
        # ============================================================
        io_frame = ttk.Frame(input_frame)
        io_frame.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=12,
            pady=8,
        )

        # uniform 保证前四列宽度完全相同。
        for column in range(4):
            io_frame.columnconfigure(
                column,
                weight=1,
                uniform="io_group",
            )

        io_frame.columnconfigure(4, weight=0)

        for index, name in enumerate(("AI", "AO", "DI", "DO")):
            group_frame = ttk.Frame(io_frame)
            group_frame.grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(0, 16),
            )
            group_frame.columnconfigure(1, weight=1)

            ttk.Label(
                group_frame,
                text=f"{name} 需求：",
            ).grid(
                row=0,
                column=0,
                padx=(0, 6),
                sticky="e",
            )

            ttk.Entry(
                group_frame,
                textvariable=self.variables[name],
                width=10,
            ).grid(
                row=0,
                column=1,
                sticky="ew",
            )

        self.calculate_button = ttk.Button(
            io_frame,
            text="计算、比较并保存",
            command=self.start_selection,
        )
        self.calculate_button.grid(
            row=0,
            column=4,
            padx=(8, 0),
            sticky="e",
        )

        # ============================================================
        # 1.3 CAD 输出选项
        # ============================================================
        cad_frame = ttk.Frame(input_frame)
        cad_frame.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=12,
            pady=(0, 6),
        )

        self.cad_check = ttk.Checkbutton(
            cad_frame,
            text="计算完成后自动输出模块组合 CAD 图（DWG/DXF，排版参数见 cad_config.json）",
            variable=self.cad_var,
        )
        self.cad_check.pack(side="left")

        # ============================================================
        # 1.4 CCU 模块选择
        # ============================================================
        ccu_frame = ttk.Frame(input_frame)
        ccu_frame.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=12,
            pady=(0, 10),
        )

        ccu_labels = {
            "main": "主站底板 CCU：",
            "ext_first": "第一块扩展底板 CCU：",
            "ext_last": "最后一块扩展底板 CCU：",
            "ext_other": "其余扩展底板 CCU：",
        }

        for index, key in enumerate(("main", "ext_first", "ext_last", "ext_other")):
            ttk.Label(
                ccu_frame,
                text=ccu_labels[key],
            ).grid(
                row=0,
                column=index * 2,
                padx=(0, 4) if index == 0 else (16, 4),
                sticky="e",
            )

            ttk.Combobox(
                ccu_frame,
                textvariable=self.ccu_vars[key],
                values=list(CCU_CHOICES),
                state="readonly",
                width=9,
            ).grid(
                row=0,
                column=index * 2 + 1,
                sticky="w",
            )

        # ============================================================
        # 2. 运行状态区域
        # ============================================================
        status_frame = ttk.Frame(self.root)
        status_frame.pack(
            fill="x",
            padx=12,
            pady=(0, 4),
        )

        self.progress = ttk.Progressbar(
            status_frame,
            mode="indeterminate",
        )
        self.progress.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, 10),
        )

        ttk.Label(
            status_frame,
            textvariable=self.status_var,
        ).pack(side="right")

        # ============================================================
        # 3. 汇总与历史相似需求
        # ============================================================
        summary_frame = ttk.LabelFrame(
            self.root,
            text="选型汇总与历史相似需求",
        )
        summary_frame.pack(
            fill="x",
            padx=12,
            pady=5,
        )

        self.summary = tk.Text(
            summary_frame,
            height=14,
            wrap="word",
        )
        summary_scrollbar = ttk.Scrollbar(
            summary_frame,
            orient="vertical",
            command=self.summary.yview,
        )
        self.summary.configure(yscrollcommand=summary_scrollbar.set)
        summary_scrollbar.pack(
            side="right",
            fill="y",
        )
        self.summary.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(8, 0),
            pady=8,
        )

        # ============================================================
        # 4. 推荐模块表格
        # ============================================================
        result_frame = ttk.LabelFrame(
            self.root,
            text="推荐模块",
        )
        result_frame.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=10,
        )

        columns = (
            "category",
            "module_name",
            "module_cname",
            "count",
            "board",
            "slots",
            "unit_price",
            "subtotal",
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

        self.tree = ttk.Treeview(
            result_frame,
            columns=columns,
            show="headings",
        )

        headings = {
            "category": "类别",
            "module_name": "模块英文名",
            "module_cname": "模块中文名",
            "count": "数量",
            "board": "底板",
            "slots": "占用槽位",
            "unit_price": "单价",
            "subtotal": "小计",
            "ai": "AI贡献",
            "ao": "AO贡献",
            "fixed_di": "固定DI",
            "fixed_do": "固定DO",
            "raw_dio": "原始DIO",
            "dio_to_di": "DIO→DI",
            "dio_to_do": "DIO→DO",
            "dio_spare": "备用DIO",
            "effective_di": "该型号贡献DI",
            "effective_do": "该型号贡献DO",
        }

        widths = {
            "category": 90,
            "module_name": 105,
            "module_cname": 145,
            "count": 58,
            "board": 60,
            "slots": 80,
            "unit_price": 85,
            "subtotal": 85,
            "ai": 70,
            "ao": 70,
            "fixed_di": 70,
            "fixed_do": 70,
            "raw_dio": 72,
            "dio_to_di": 72,
            "dio_to_do": 72,
            "dio_spare": 72,
            "effective_di": 95,
            "effective_do": 95,
        }

        for column in columns:
            self.tree.heading(
                column,
                text=headings[column],
            )
            self.tree.column(
                column,
                width=widths[column],
                minwidth=50,
                anchor="center",
            )

        vertical_scrollbar = ttk.Scrollbar(
            result_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        horizontal_scrollbar = ttk.Scrollbar(
            result_frame,
            orient="horizontal",
            command=self.tree.xview,
        )

        self.tree.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )

        self.tree.grid(
            row=0,
            column=0,
            sticky="nsew",
        )
        vertical_scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
        )
        horizontal_scrollbar.grid(
            row=1,
            column=0,
            sticky="ew",
        )

        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)

        self.name_entry.focus_set()

    def _read_requirements(self):
        """读取名称和 AI/AO/DI/DO，并检查输入。"""
        requirement_name = self.name_var.get().strip()

        if not requirement_name:
            raise ValueError("名称不能为空。")

        if len(requirement_name) > 200:
            raise ValueError("名称不能超过 200 个字符。")

        values = {}

        for name, variable in self.variables.items():
            text = variable.get().strip()

            try:
                value = int(text)
            except ValueError as exc:
                raise ValueError(
                    f"{name} 必须输入整数。"
                ) from exc

            if value < 0:
                raise ValueError(
                    f"{name} 不能是负数。"
                )

            values[name] = value

        return requirement_name, values

    def start_selection(self):
        """点击按钮后启动后台求解线程。"""
        try:
            requirement_name, requirements = self._read_requirements()
        except ValueError as exc:
            messagebox.showerror(
                "输入错误",
                str(exc),
            )
            return

        ccu_choices = {
            key: variable.get()
            for key, variable in self.ccu_vars.items()
        }

        self.calculate_button.configure(state="disabled")
        self.progress.start(10)
        if self.cad_var.get():
            self.status_var.set(
                "正在运行二维数组动态规划并保存历史，完成后输出 CAD 组合图……"
            )
        else:
            self.status_var.set(
                "正在运行二维数组动态规划并保存历史……"
            )
        self.start_time = time.perf_counter()

        worker = threading.Thread(
            target=self._worker,
            args=(requirement_name, requirements, ccu_choices),
            daemon=True,
        )
        worker.start()

    def _worker(self, requirement_name, requirements, ccu_choices):
        """后台线程执行选型、历史比较、数据库保存和 CAD 生成。"""
        try:
            result = select_modules_and_save(
                requirement_name=requirement_name,
                req_ai=requirements["AI"],
                req_ao=requirements["AO"],
                req_di=requirements["DI"],
                req_do=requirements["DO"],
                main_ccu=ccu_choices["main"],
                ext_first_ccu=ccu_choices["ext_first"],
                ext_last_ccu=ccu_choices["ext_last"],
                ext_other_ccu=ccu_choices["ext_other"],
            )
        except Exception as exc:
            self.root.after(
                0,
                self._finish_error,
                exc,
            )
            return

        cad_result = None
        cad_elapsed = 0.0

        if self.cad_var.get():
            def report(message):
                self.root.after(
                    0,
                    lambda m=message: self.status_var.set(m),
                )

            cad_started = time.perf_counter()
            cad_result = generate_combined_cad(
                result,
                requirement_name,
                progress=report,
            )
            cad_elapsed = time.perf_counter() - cad_started

        self.root.after(
            0,
            self._finish_success,
            result,
            cad_result,
            cad_elapsed,
        )

    def _finish_error(self, error):
        self.progress.stop()
        self.calculate_button.configure(state="normal")
        self.status_var.set("求解失败")

        if isinstance(error, SelectorError):
            messagebox.showerror(
                "选型失败",
                str(error),
            )
        else:
            messagebox.showerror(
                "程序错误",
                f"{type(error).__name__}: {error}",
            )

    def _finish_success(self, result, cad_result, cad_elapsed):
        elapsed = time.perf_counter() - self.start_time

        self.progress.stop()
        self.calculate_button.configure(state="normal")

        if cad_result is None:
            self.status_var.set(f"完成，用时 {elapsed:.3f} 秒")
        else:
            self.status_var.set(
                f"完成，选型 {elapsed:.3f} 秒，"
                f"CAD 输出 {cad_elapsed:.1f} 秒"
            )

        self._show_result(result, elapsed, cad_result)

        if cad_result is not None and cad_result["success"]:
            if messagebox.askyesno(
                "CAD 输出完成",
                "模块组合 CAD 图已生成。\n是否打开输出文件夹？",
            ):
                os.startfile(cad_result["folder"])  # noqa: S606

    def _show_result(self, result, elapsed, cad_result=None):
        requirement = result["requirement"]
        actual = result["actual"]

        text = (
            f"名称：{result['requirement_name']}\n"
            f"历史记录ID：{result['history_id']}（当前结果已保存）\n"
            f"算法：{result['algorithm']}\n"
            f"需求：AI={requirement['ai']}，AO={requirement['ao']}，"
            f"DI={requirement['di']}，DO={requirement['do']}\n"
            f"实际：AI={actual['ai']}，AO={actual['ao']}，"
            f"DI={actual['di']}，DO={actual['do']}\n"
            f"DIO：总数={actual['raw_dio']}，"
            f"给DI={actual['dio_to_di']}，"
            f"给DO={actual['dio_to_do']}，"
            f"备用={actual['dio_spare']}\n"
            f"AIO784 选中数量={result['selected_aio784_count']}\n"
            f"模拟量最低价={result['analog_price']:.2f}，"
            f"数字量最低价={result['digital_price']:.2f}\n"
            f"模块总数={result['module_count']}，"
            f"总价格={result['total_price']:.2f}，"
            f"求解时间={elapsed:.3f} 秒"
        )

        rack = result.get("rack")
        if rack:
            ccu_text = "，".join(
                f"{name} ×{count}"
                for name, count in rack.get("ccu_counts", {}).items()
            )
            text += "\n\n机架组装："
            for board in rack["boards"]:
                kind = "主站" if board["is_main"] else "扩展"
                text += (
                    f"\n  底板{board['board_index']}（{kind}）"
                    f"{board['module_name']}，{board['slots']} 槽位，"
                    f"CCU={board.get('ccu_module', '')}，"
                    f"含 IO 模块 {board['io_module_count']} 个，"
                    f"DMM700 填充 {board['dmm_count']} 个\n"
                    f"    {board['layout']}"
                )
            text += (
                f"\n  MPU710 ×1，CCU：{ccu_text}，"
                f"{rack['pwr_module']} ×{rack['pwr_count']}，"
                f"{rack['dmm_module']} ×{rack['dmm_total']}，"
                f"底板共 {rack['board_count']} 块"
            )

        closest = result.get("closest_history")

        if closest is None:
            text += (
                "\n\n历史比较：这是第一条历史记录，"
                "暂无可以比较的历史需求。"
            )
        else:
            old_requirement = closest["requirement"]
            delta = closest["delta"]

            text += (
                "\n\n最接近的历史需求："
                f"{closest['requirement_name']} "
                f"（ID={closest['id']}，"
                f"创建时间={closest['created_at']}）\n"
                f"历史需求："
                f"AI={old_requirement['ai']}，"
                f"AO={old_requirement['ao']}，"
                f"DI={old_requirement['di']}，"
                f"DO={old_requirement['do']}\n"
                f"当前－历史差值："
                f"ΔAI={delta['ai']:+d}，"
                f"ΔAO={delta['ao']:+d}，"
                f"ΔDI={delta['di']:+d}，"
                f"ΔDO={delta['do']:+d}\n"
                f"归一化四维距离="
                f"{closest['normalized_distance']:.6f}，"
                f"参考相似度="
                f"{closest['similarity_percent']:.2f}%\n"
                f"历史方案："
                f"{closest['module_summary'] or '未读取到模块摘要'}；"
                f"总价={closest['total_price']:.2f}"
            )

        if result["warnings"]:
            text += (
                "\n提示："
                + "；".join(result["warnings"])
            )

        if cad_result is not None:
            if cad_result["success"]:
                text += (
                    "\n\nCAD 组合图输出：\n"
                    f"文件夹：{cad_result['folder']}\n"
                )
                if cad_result["dwg_path"]:
                    text += f"DWG：{cad_result['dwg_path']}\n"
                if cad_result["dxf_path"]:
                    text += f"DXF：{cad_result['dxf_path']}\n"
                text += (
                    f"图中绘制模块数：{cad_result['placed_count']}"
                )
                for warning in cad_result["warnings"]:
                    text += f"\nCAD提示：{warning}"
            else:
                text += (
                    "\n\nCAD 组合图生成失败："
                    f"{cad_result['error']}"
                )

        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)

        for item in self.tree.get_children():
            self.tree.delete(item)

        for row in result["rows"]:
            self.tree.insert(
                "",
                "end",
                values=(
                    row["category"],
                    row["module_name"],
                    row["module_cname"],
                    row["count"],
                    row.get("board", ""),
                    row.get("slots", ""),
                    f"{row['unit_price']:.2f}",
                    f"{row['subtotal']:.2f}",
                    row["ai"],
                    row["ao"],
                    row["fixed_di"],
                    row["fixed_do"],
                    row["raw_dio"],
                    row["dio_to_di"],
                    row["dio_to_do"],
                    row["dio_spare"],
                    row["effective_di"],
                    row["effective_do"],
                ),
            )


def main():
    root = tk.Tk()
    ModuleSelectorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

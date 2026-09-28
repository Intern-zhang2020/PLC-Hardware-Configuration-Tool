# I/O 模块选型：二维数组动态规划完整版

## 本版包含的功能

- `dp[AI][AO]` 使用真正的二维列表保存状态；
- `dp[DI][DO]` 使用真正的二维列表保存状态；
- 不再使用 `best[(ai, ao)]`、`parent[(di, do)]` 这样的字典保存 DP 状态；
- AIO784 同时参与 AI/AO 联合选型；
- DIO 自动分配为 DI、DO 和备用 DIO；
- 优先保证总价格最低，价格相同时模块数量最少；
- GUI 包含名称输入框，AI/AO/DI/DO 间距统一；
- 自动保存历史需求和模块明细；
- 使用 AI/AO/DI/DO 四维归一化欧氏距离查找最接近历史需求；
- 历史数据库采用规范化的主表和明细表；
- **DP 选型完成后自动组装底板机架**（MPU710/CCU/电源/DMM700 填充 + BSM/BSE 底板）；
- **CCU 模块可按底板位置选择**（主站/第一块扩展/最后一块扩展/其余扩展，CCU700/CCU701/CCU702）；
- **计算完成后自动输出模块组合 CAD 图（DWG + DXF，按底板分行）**；
- **模块 CAD 图纸存储在数据库 `module_cad` 表**，日常运行不依赖本地 DWG 文件。

## 机架组装规则

DP 求解出 IO 模块组合后，`rack_builder.py` 自动完成机架组装：

1. **主站底板**（`BSM` 开头，每次选型只选一块）
   - 第 1-2 槽：`MPU710`（必选，占用 2 个槽位，只能放在 BSM 主站底板上）；
   - 第 3 槽：CCU 模块（默认 `CCU702`，可在界面上下拉选择）；
   - 之后按 DP 结果顺序装入 IO 模块；
   - 空槽用 `DMM700`（空槽位填充模块，占 1 槽）补齐；
   - 最后 1 槽固定放电源模块（默认 `PWR750`）。
2. **扩展底板**（`BSE` 开头，主站装不下时自动追加，数量不限）
   - 第 1 槽：CCU 模块（默认 `CCU702`），扩展底板不能放 MPU710；
   - 之后：剩余 IO 模块，空槽用 `DMM700` 补齐，最后 1 槽放 `PWR750`。
3. **底板型号**按“能装下的最小槽位数”选择，槽位数取型号最后两位数字：
   - 主站：`BSM706`(6) ~ `BSM716`(16)；
   - 扩展：`BSE705`(5) ~ `BSE714`(14)。
4. **槽位占用**：`MPU710`、`DIM780` 占 2 个槽位，其他模块占 1 个槽位。
5. 底板本身不出现在 CAD 组合图中，但会出现在选型结果（表格“底板”列、机架摘要和价格）里。

## CCU 模块选择

CCU 有三种型号：`CCU700`（2 光口）、`CCU701`（1 光 1 电）、`CCU702`（2 电口），
默认全部使用 `CCU702`。GUI 提供 4 个下拉框按底板位置分别选择：

| 下拉框 | 作用范围 |
| --- | --- |
| 主站底板 CCU | 主站底板（BSM）第 3 槽 |
| 第一块扩展底板 CCU | 第一块扩展底板（BSE）第 1 槽 |
| 最后一块扩展底板 CCU | 最后一块扩展底板（仅扩展底板数 ≥ 2 时生效） |
| 其余扩展底板 CCU | 中间的扩展底板 |

只有一块扩展底板时按“第一块扩展底板”的选择执行。
CLI 对应参数：`--main-ccu`、`--ext-first-ccu`、`--ext-last-ccu`、`--ext-other-ccu`。

## 文件说明

```text
selector_core.py          数据库读取、二维数组 DP、历史保存和距离比较
rack_builder.py           底板机架组装（MPU710/CCU/电源/DMM700 + BSM/BSE 底板选择）
cad_layout.py             模块组合 CAD 图生成（从数据库读取模块 DXF、按底板分行排版、DXF→DWG 转换）
import_cad_to_db.py       把本地“7000模块正面CAD”目录的 DWG 导入数据库 module_cad 表
cad_config.json           CAD 输出配置（源目录、缓存、排版参数等，首次运行自动生成）
gui_app.py                图形界面（CCU 下拉选择、表格含底板/槽位列、汇总含机架布局）
cli_app.py                命令行界面（CCU 参数、打印底板布局、--no-cad 关闭 CAD 输出）
db_config.json            MySQL 配置
create_history_tables.sql 历史表结构
test_array_dp.py          二维数组 DP 与大状态保护测试
test_aio784.py            AIO784 测试
test_history_distance.py  历史距离测试
```

## 安装

```powershell
python -m pip install -r requirements.txt
```

打开 `db_config.json`，确认 MySQL 连接信息：

```json
{
    "host": "192.168.5.100",
    "user": "root",
    "password": "123",
    "database": "ProductInfo",
    "port": 49155
}
```

## 测试

```powershell
python test_array_dp.py
python test_aio784.py
python test_history_distance.py
```

## 启动 GUI

```powershell
python gui_app.py
```

## CAD 组合图输出

模块 CAD 图纸存储在数据库表 `module_cad` 中（DXF 内容，zlib 压缩；
由 `import_cad_to_db.py` 从本地“7000模块正面CAD”目录导入，
34.4 MB 原始 DXF 压缩后约 3.1 MB）。

更新图纸时：替换本地目录中的 DWG，重新运行 `python import_cad_to_db.py` 即可。

选型计算完成后（GUI 勾选“计算完成后自动输出模块组合 CAD 图”，CLI 默认开启、
`--no-cad` 关闭），程序会：

1. 按选型结果从数据库 `module_cad` 表读取所需模块的 DXF，
   解压后落到 `cad_cache_dxf` 缓存目录；
2. 按选型结果排版成组合图：机架模式下**每块底板的模块占一行**
   （行下方标注 `底板N：BSMxxx（N 槽位）`），模块上方标注型号分组；
3. 输出到 `cad_output\<需求名称>_<时间戳>\`，同时提供 DWG 和 DXF 两种格式。

`cad_config.json` 可调整的参数：

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `cad_source_dir` | 模块 DWG 正面图源目录（仅 import_cad_to_db.py 导入时使用） | `../../7000模块正面CAD` |
| `dxf_cache_dir` | 从数据库读取后的 DXF 缓存目录 | `cad_cache_dxf` |
| `output_dir` | 组合图输出目录 | `cad_output` |
| `oda_converter_path` | ODAFileConverter.exe 位置 | `../../ODAExtract/ODAFileConverter.exe` |
| `modules_per_row` | 每行摆放的模块数 | `16` |
| `module_gap_mm` | 模块水平间距（mm） | `7.5` |
| `row_gap_mm` | 行间距（mm） | `40` |
| `margin_mm` | 图纸边距（mm） | `40` |
| `dwg_version` | 输出 DWG 版本 | `ACAD2018` |
| `keep_dxf` | 是否同时保留 DXF 文件 | `true` |

相对路径一律相对本程序目录解析。

依赖说明：

- 导入图纸（DWG→DXF）和输出 DWG 需要免费的 **ODA File Converter**
  （Open Design Alliance），程序按 `oda_converter_path` →
  `C:\Program Files\ODA\...` → 系统 PATH 的顺序自动查找；
  找不到时输出 DXF 并给出提示（DXF 同样可用 AutoCAD 打开）。
- 日常生成组合图不需要本地 DWG 文件，只依赖数据库。
- 模块名与图纸记录对应（不区分大小写），例如 `MPU710` ↔ `mpu710`。
  某个模块在数据库中没有 CAD 记录时会在结果中提示并跳过，不影响其他模块。

## 二维数组结构

模拟量使用：

```python
best_cost[ai][ao]
best_count[ai][ao]
parent_ai[ai][ao]
parent_ao[ai][ao]
parent_module[ai][ao]
```

数字量使用：

```python
best_cost[di][do]
best_count[di][do]
parent_di[di][do]
parent_do[di][do]
parent_option[di][do]
```

字典仍用于表示一条模块记录、数据库查询结果和最终汇总；这些不是 DP 状态表。

## 内存保护

二维数组会一次性分配完整状态空间。程序顶部设置：

```python
MAX_ARRAY_DP_STATES = 1_500_000
```

当 `(DI+1) × (DO+1)` 或 `(AI+1) × (AO+1)` 超过该值时，程序会给出错误提示，而不是继续分配内存。

例如 `DI=9000、DO=9000` 需要约 8100 万个状态，因此数组版会主动阻止。修改安全上限可以允许更大状态，但有明显内存风险。

## 历史表

主表：

```text
selection_history
```

模块明细表：

```text
selection_history_module
```

查看简洁历史：

```sql
SELECT * FROM selection_history_overview;
```

查看某次选型的模块：

```sql
SELECT *
FROM selection_history_module_overview
WHERE history_id = 1;
```

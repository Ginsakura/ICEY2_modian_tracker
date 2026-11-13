## Why

现有「档位结构占比」只用份数计算百分比：它准确回答了“多少人买了哪个档位”（Lv2 占 51.4%），却完全看不到“钱主要来自哪个档位”。按金额口径，Lv9 以 13.6% 的份数贡献了 51.3% 的金额，Lv2 以 51.4% 的份数只贡献 14.8%——两个口径给出的结构判断几乎相反。众筹已进入长尾（截至 2026-11-11 累计 4,077.6 万元，截止 11-15），后续的档位备货、履约成本与传播重点都需要按金额而不是份数判断；而现有窗口里没有任何一页能直接看到这个结构。

金额口径可以直接从现有数据算出：Lv1~Lv9 的单价就在列名里，金额 = 单价 × 份数；无偿档位没有单价，支持者自行填写金额，所以金额 = 总金额 − 全部付费档位金额。用 `data.csv` 全表 51,626 个有效采样验证，残差始终为正（6.20 万~8.34 万元，占总额 0.20%~0.23%），最新残差 ÷ 无偿份数 ≈ 13.35 元/份，与“自行填写金额”的量级吻合。数据基础已经具备，只差一张按金额口径复刻的占比页。

## What Changes

- 新增第 10 个分析视图「金额结构占比」，插在「档位结构占比」之后；左侧 100% 堆积面积图，右侧环形快照，快照时刻由现有滑块选择。
- 新增金额口径数据助手：付费档位金额 = 表头单价 × 份数；无偿金额 = 总金额 − 付费档位金额之和；各档占比 = 各档金额 ÷ 可见档位金额之和。按表头所有 `Lv*（￥…）` 列计算，不硬编码 9 个档位。
- 将 `_draw_share_view` / `_draw_snapshot` 参数化为 `count` / `money` 两种口径，份数版渲染结果与交互保持不变。
- 视图选择器从 9 项扩到 10 项，左侧面板高度从 0.285 抬到约 0.32；档位显隐与快照滑块适用范围扩展到新视图。
- 环形图中心显示可见档位合计金额（万元），图例同时给百分比与金额；面积图 tooltip 同时给百分比与金额。
- 同步更新 `scripts/render_preview.py`、`scripts/verify_views.py` 与 `docs/analyze_spec.md` 中「九个视图」的计数和视图清单。
- 不改变：份数版「档位结构占比」、CSV 列结构、`--plot` 行为、采集端、依赖。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `analysis-view-suite`: 视图数量由九个扩到十个；新增「金额结构占比」的金额口径、无偿残差算法、环形快照、显隐重归一化与数据兜底要求；快照滑块与档位显隐的适用范围扩展到该视图。

## Impact

- 代码：`charts.py`（`BaseChart` 新增金额占比助手；`AnalyzeChart.VIEWS`、`TIER_APPLIES`、`_sync_control_state`、`_on_snap_change`、`_draw_share_view`、`_draw_snapshot`、`render_view`、视图单选矩形）。
- 脚本：`scripts/render_preview.py` / `scripts/verify_views.py` 的文案与计数；渲染循环本身按 `VIEWS` 自动覆盖新视图。
- 文档：`docs/analyze_spec.md` 的九视图表、控件适用范围与验收标准。
- 数据：无迁移；`data.csv` 列序与格式不变。无新增依赖，仍只用 matplotlib/numpy/标准库。
- 风险：金额占比的时间迁移很小（Lv9 全期 +5.8pp，其余档位多在 2pp 内），面积图会偏“平”，主要信息量在环形图；隐藏 Lv9 后按可见档位重归一化可能放大其余档位，需在标题/图例中明确分母。
- 前置状态：`csv-append-and-analysis-views` 已完成但尚未归档，`openspec/specs/` 目前为空；本 change 的 `analysis-view-suite` delta 以该 change 的 delta spec 为基线，归档本 change 前需先归档前置 change，否则 `MODIFIED Requirements` 找不到基线。

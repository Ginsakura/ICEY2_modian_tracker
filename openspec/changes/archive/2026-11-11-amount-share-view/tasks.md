## 1. 准备与数据层

- [x] 1.1 归档前置 change：确认 `openspec/specs/analysis-view-suite/spec.md` 已存在；若不存在，先运行 `openspec archive csv-append-and-analysis-views --yes`，否则本 change 归档时 `RENAMED/MODIFIED` 找不到基线。
- [x] 1.2 在 `BaseChart` 增加金额占比助手 `amount_share_matrix(labels)`：返回 `(xs, pct_series, amount_series, totals)`；付费档位金额 = 列名单价 × 份数，无偿金额 = 总金额 − 全部付费档位金额，分母 = 可见档位金额之和。
- [x] 1.3 给助手补数据兜底：总金额缺失/非正、残差为负、价格解析失败的行跳过并计数；缺档位列按 0 份；无有效行时返回空结果。
- [x] 1.4 用现有 `data.csv` 离线核对：最新行 Lv9 金额 20,903,090 元、无偿 83,400.32 元、占比 51.26%/0.20%；全表 51,626 行、残差 62,047~83,415 元。

## 2. 核心实现

- [x] 2.1 新增 `VIEW_SHARE_AMOUNT = "金额结构占比"`，在 `VIEWS` 中插到「档位结构占比」之后；`render_view` 的 drawer 映射到参数化的占比绘制。
- [x] 2.2 把 `_draw_share_view` / `_draw_snapshot` 参数化为 `count` / `money`；份数版保持原百分比、图例、快照与档位配色。
- [x] 2.3 金额版面积图：标题「档位金额占比迁移(占可见档位金额之和)」，y 轴 0~100；tooltip 每档给「百分比 + 万元」。
- [x] 2.4 金额版环形图：中心「合计 X 万元」；图例形如 `Lv9 (￥598) 51.26% (2,090.3万)`；低于 3% 的扇区不标百分比，由图例兜底。
- [x] 2.5 扩展适用性：`TIER_APPLIES` 加入新视图；`_sync_control_state` 的 `snap_on` 覆盖两个占比视图；`_on_snap_change` 在两个占比视图下都只重绘环形面板。
- [x] 2.6 调整视图面板矩形到约 `(0.015, 0.60, 0.145, 0.32)`，确认 10 项 8pt 文字不与聚合粒度面板（0.475~0.58）重叠。

## 3. 验证与收尾

- [x] 3.1 运行 `scripts/render_preview.py` 渲染全部视图，检查 `金额结构占比.png`：0~100% 堆积、中心/图例、Lv1 与无偿的极小扇区、无文字重叠。
- [x] 3.2 回归份数版：对比变更前后的 `档位结构占比.png`，百分比、图例、环形快照不变；切回时显隐状态不被金额版改写。
- [x] 3.3 手动开 `--analyze` 验证：切视图保留时间窗口/显隐/快照；拖快照只重绘环；隐藏 Lv9 后按可见金额重归一化；全部隐藏时给占位提示。
- [x] 3.4 边界验证：用临时 CSV 覆盖总金额缺失、负残差、缺档位列、价格无法解析四种情况，确认跳过/按 0 份/提示行数且不崩溃。
- [x] 3.5 运行 `python modian_tracker.py --plot`，确认基础趋势图未受参数化改动影响。
- [x] 3.6 更新文案：`scripts/render_preview.py` 九→十、`scripts/verify_views.py` 九→十、`docs/analyze_spec.md` 的视图表/控件适用范围/验收标准。
- [x] 3.7 运行 `openspec validate amount-share-view`，确认只剩“前置归档”INFO；勾选已完成任务，准备 apply/archive。

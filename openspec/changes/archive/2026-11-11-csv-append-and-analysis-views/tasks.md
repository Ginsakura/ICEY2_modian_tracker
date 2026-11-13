## 1. 采集端追加写入

- [x] 1.1 在 `modian_tracker.py` 增加 `_expected_header(data)` 与 `_read_header()`：后者只读文件首行并返回表头列表
- [x] 1.2 增加 `_check_file_format()`：读取首 3 字节判断 BOM、末 1~2 字节判断 CRLF 与半行，返回「可直接追加 / 需重写」结论
- [x] 1.3 增加 `_truncate_partial_line()`：末字节不是 `\n` 时从末尾分块向前找最后一个换行并 `truncate`，记录 warning
- [x] 1.4 增加 `_format_row(header, data, total_count, total_money)`：复用 `_clean_cell` 规则生成单元格，缺值写空字符串，列位不移动
- [x] 1.5 重写 `append_to_csv` 常规路径：格式校验 → 半行修复 → 渲染整行 → `open(..., "ab")` 一次写入 UTF-8 字节
- [x] 1.6 把 `_write_csv` 改造成 `_write_csv_atomic(rows, money_idx, path)`：写 `data.csv.tmp`、flush 后 `os.replace`，异常时清理临时文件
- [x] 1.7 实现重写回退触发条件：文件不存在或为空、表头缺列、BOM 或行尾不符；重写完成后重新走一次格式校验
- [x] 1.8 新增 `scripts/verify_append.py`（仅标准库）：覆盖首建文件、常规追加、表头演进、半行修复、被外部改成 LF、重写中途失败不损坏原文件六种情形
- [x] 1.9 在 `data.csv` 副本上跑一次真实追加，校验行数 +1、历史前缀字节哈希不变、文件末尾为 CRLF、BOM 仍只出现一次
- [x] 1.10 记录改造前后单次采样的耗时与读写字节数对比（各取 5 次均值），写入提交说明

## 2. 数据助手

- [x] 2.1 `BaseChart` 增加 `window_bounds(lo, hi)`：把滑块的起止索引换算为时间戳，供新视图切片使用
- [x] 2.2 增加 `daily_totals(lo, hi)`：按跨日末值差返回 `[(date, Δ人数, Δ金额)]`，缺整日跳过
- [x] 2.3 增加 `flow_buckets(seconds, lo, hi)`：先在原始相邻采样上取正/负增量，再按桶求和，返回金额与人数两套毛增、回吐、净增
- [x] 2.4 增加 `void_rates(lo, hi)`：逐档毛增份数、回吐份数与回吐率；毛增为 0 的档返回 `None`
- [x] 2.5 增加 `marginal_share(seconds, lo, hi)`：每桶各档毛增人数与份额矩阵（负增量不入分母）
- [x] 2.6 增加 `cumulative_share(lo, hi)`：窗口末各档占档位人数总和的累计份额
- [x] 2.7 增加 `rhythm_matrix(lo, hi)`：日 × 时金额增量百分比矩阵、缺采掩码、小时均值、峰值小时与负值小时
- [x] 2.8 增加 `finale_forecast(fit_days=7, deadline=DEADLINE)`：7 日均值等差与对数线性两种外推、截止总额、包络下限/上限、下一整百万关口 ETA
- [x] 2.9 用现有 `data.csv` 抽检助手数值：金额回吐率约 9.7%（回吐 ÷ 窗口毛增；若以累计总额为分母则约 3.3%）、Lv8 回吐率约 41%、Lv9 累计份额约 13.4%

## 3. 视图实现

- [x] 3.1 `AnalyzeChart.VIEWS` 增加四个视图常量，`render_view` 的分发改成显式映射，避免长 if/elif 链
- [x] 3.2 调整 `_ax_view` 矩形与标签字号（容纳 9 项），确认不与聚合粒度面板重叠、不超出 figure 顶部
- [x] 3.3 扩展 `GRAN_APPLIES` 与 `_sync_control_state`：聚合粒度适用「增速、客单价、净流、边际」，档位显隐适用「趋势、占比、净流、边际」
- [x] 3.4 实现 `_draw_flow_view`：上子图为毛增/回吐柱 + 净增线（0 轴、金额格式化）；下子图为各档回吐率横条，无毛增档标注 N/A
- [x] 3.5 实现 `_draw_margin_view`：上子图为边际份额 100% 堆积；下子图为各档累计份额与窗口边际份额并列横条
- [x] 3.6 实现 `_draw_rhythm_view`：上子图为日 × 时热力图（缺采用 `set_bad` 样式、色标、日与小时刻度）；下子图为小时均值曲线并标注峰值与负值小时
- [x] 3.7 实现 `_draw_finale_view`：上子图为日金额柱 + 3 日均线 + 阶段背景带与标签；下子图为累计曲线 + 双模型外推包络 + 截止竖线 + 下一关口 ETA
- [x] 3.8 为四个新视图绑定 `CursorTracker`，provider 输出该视图的逐点读数
- [x] 3.9 处理边界：空窗口、单日数据、缺整日、数据末行已过截止时刻，均走 `_notice` 或留空，不抛异常
- [x] 3.10 视图切换与重置后调用 `_sync_control_state`，新增视图的控件置灰与标注行为一致

## 4. 验证与收尾

- [x] 4.1 Windows 侧冒烟：`python -c "import charts"`、`python modian_tracker.py --analyze` 能打开窗口
- [x] 4.2 无头渲染九个视图到 `.preview/`（Agg 后端），逐张检查标题、坐标、图例、空态与中文显示
- [x] 4.3 回归：五个既有视图在同一数据窗口下的渲染结果与变更前一致（PNG 或像素数组哈希对比）
- [x] 4.4 读取端兼容：用被截断半行的 `data.csv` 副本验证 `BaseChart.load` 不报错且跳过残缺行
- [x] 4.5 高密度模拟：临时文件按 10 秒间隔追加 500 行，确认追加耗时恒定、`--analyze` 轮询重载可用
- [x] 4.6 更新 `docs/analyze_spec.md`：补四个新视图的口径说明与控件适用矩阵
- [x] 4.7 跑 `openspec validate csv-append-and-analysis-views --strict` 并保持 `openspec status` 全部 done
- [x] 4.8 采集进程在真实页面跑满一个采样周期，确认日志只出现常规追加、git 同步无异常

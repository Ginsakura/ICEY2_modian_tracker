# 实施任务

## 1. 配置基础

- [x] 1.1 确认 git 工作区基线，记录 commit 65dd85c 作为回滚点
- [x] 1.2 新增 trackers.json：version/defaults/projects；icey2 使用 data/icey2.csv、collect_until 2026-11-16 00:00、五个 phases；linglan 使用 data/linglan.csv、collect_from 2026-11-15 00:00、collect_until 2027-01-10 12:00
- [x] 1.3 新增 tracker_config.py：ProjectConfig、CampaignConfig、Phase 数据类，以及 load_config、default_project、atomic_save
- [x] 1.4 在 tracker_config.py 实现全部校验：项目名正则与大小写不敏感唯一、仅摩点 item URL、data_file 相对路径且以 .csv 结尾、不同项目 data_file 不得同文件、interval 30-3600、campaign 日期与 phases 顺序、collect_from 早于 collect_until
- [x] 1.5 新增 tier_columns.py：format_tier_name（去 LV 前缀、空标题省略、全角括号）、parse_price（锚定列名末端）、strip_lv_prefix
- [x] 1.6 新增 scripts/verify_config.py，覆盖合法配置与重名、绝对路径、同文件、非摩点链接、phases 逆序、窗口逆序等非法样例

## 2. 采集端

- [x] 2.1 把 modian_tracker.py 的抓取与解析改为返回 Tier 对象（price_text、Decimal 价格、title、count、free），供采集器与 manage.py 复用
- [x] 2.2 实现 resolve_columns 与按项目 data_file 的追加路径：价格匹配旧列、标题变化沿用旧列、缺列时原子重写并补空
- [x] 2.3 实现 Collector 线程：stop Event、next_at 计时、collect_from 之前分片等待且不发请求、collect_until 之后结束、单项目失败记录后按间隔重试
- [x] 2.4 实现多项目启动：每启用项目一个线程、全局单个 sync 线程、日志名 collector:<project>、run.pid 写入与退出删除、SIGINT/SIGTERM 处理、所有线程结束后退出
- [x] 2.5 实现 --once：按窗口逐项目处理，窗口内抓取一轮，窗口外标记 skipped，打印项目名与结果汇总，不写 run.pid、不启 sync
- [x] 2.6 调整 CLI：移除 --interval；--plot 与 --analyze 增加 --project
- [x] 2.7 追加前自动创建 data_file 的父目录（如 data/）
- [x] 2.8 删除旧单项目兼容入口：URL/CSV_FILE 全局、parse_tier_data、fetch_tier_data、append_to_csv、_format_row、_rewrite_csv 及默认 data.csv 分支
- [x] 2.9 用临时配置与临时 CSV 执行 --once --project icey2，确认新格式表头、9 个档位与写入内容正确，真实 data.csv 未变

## 3. 图表

- [x] 3.1 新增 ChartContext，并让 BaseChart、AnalyzeChart、PlotChart 接受 csv_file 与 project，按 csv_file > project > defaults.project > 第一个项目的顺序解析
- [x] 3.2 把 DEADLINE、PLOT_X_LEFT、CAMPAIGN_PHASES、MILLION_STEP、TENMILLION_STEP 的全部使用点替换为 ChartContext 字段
- [x] 3.3 实现 nice_nearest 与自适应步长：max/40 取最接近的 {1,2,2.5,5}×10^n；campaign.milestone_step 存在时覆盖；major_step=10×step
- [x] 3.4 实现金额单位与格式化助手：按量级切换 元/万元/百万元；百万元模式保留 0.5/0.1 刻度，其余按数据范围生成
- [x] 3.5 修改 _lv_no 兼容 Lv.3、tier_prices 使用末端价格并按价格排序、lv_cols 按 (价格, Lv) 排序、图例标签压缩正则
- [x] 3.6 实现空 phases（不画灰带与阶段日均）、空 deadline（不外推、不画截止线）、空 start（用数据首行）三个分支
- [x] 3.7 更新 scripts/render_preview.py 与 scripts/verify_views.py 的构造调用
- [x] 3.8 用 render_preview 对 icey2 旧表头与 linglan 合成数据各渲染十视图，确认里程碑、截止线、阶段文本、档位与单位；十视图均成功

## 4. 配置工具

- [x] 4.1 新增 manage.py 子命令：list、check、preview、add、disable，非法配置时非零退出
- [x] 4.2 实现交互菜单：列表、新增、修改、停用、预览、退出，字段编辑支持循环返回
- [x] 4.3 preview 复用采集端抓取与解析，输出时间戳、总人数、总金额、档位数量与生成的列名；失败时中止保存
- [x] 4.4 保存采用临时文件加 os.replace，并原样保留 phases、milestone_step、collect_from/collect_until 等未管理字段
- [x] 4.5 启动时把 stdout/stderr 设为 UTF-8，避免 Windows 控制台打印全角￥报错
- [x] 4.6 在 temp/trackers_test.json 上非交互 add/disable/check，确认预览、保存、大小写不敏感停用均正确；真实 trackers.json 未改

## 5. 启动脚本

- [x] 5.1 改造 run：保留 start/stop/restart/state，移除 interval 参数，start 后等待 run.pid 出现
- [x] 5.2 改造 run.ps1：同样的动作与 PID 轮询，移除 interval 参数与弃用提示；恢复 UTF-8 BOM 后 -Action state 通过
- [x] 5.3 检查 .gitignore 不会忽略 data/ 目录与 data/linglan.csv、data/icey2.csv

## 6. 验证与收尾

- [x] 6.1 py_compile 通过：manage.py、modian_tracker.py、charts.py、tracker_config.py、tier_columns.py、shared_logger.py、scripts/verify_*.py、scripts/render_preview.py
- [x] 6.2 运行 scripts/verify_append.py，六个 append_tiers 用例全部通过，真实 data.csv 未变
- [x] 6.3 运行 scripts/verify_views.py，十个视图、截断行、高密度追加与档位价格排序兼容，7 PASS/0 FAIL
- [x] 6.4 运行 scripts/verify_config.py 与 scripts/verify_tiers.py，分别 32 PASS/0 FAIL、7 PASS/0 FAIL
- [x] 6.5 运行 scripts/render_preview.py 对 icey2 与 linglan 合成数据各渲染十视图，全部成功
- [x] 6.6 验证窗口、并发、--once 与日志项目名；未到 collect_from 不发请求，越过 collect_until 不写入
- [x] 6.7 更新 docs/analyze_spec.md：删除 interval 参数弃用旧说法，配置驱动、项目路径、截止时间、自适应步长与双项目验收全部按最终版描述
- [x] 6.8 更新 proposal.md、design.md、specs：删除桥接、migrate_columns、兼容层与 16 号迁移流程，改为 data/icey2.csv + 手机手动移动
- [x] 6.9 openspec validate --strict 通过（0 issue）
- [x] 6.10 清理 temp/ 测试产物与桥接残留（linglan_bridge.pid、linglan_log.log）；保留 data.csv、tracker_log.log 与 logs/ 历史产物；run.pid、__pycache__、modian_tracker.log 受工作区 ACL 限制未能删除，留待手机切换前清理；data.csv SHA256 保持 D51C5D44…

## 7. 手机切换（待执行）

- [ ] 7.1 手机端 ./run stop，确认旧采集进程已退出
- [ ] 7.2 同步最终代码；mkdir -p data；mv data.csv data/icey2.csv；确认 trackers.json 的 icey2.data_file 为 data/icey2.csv
- [ ] 7.3 ./run start；./run state 与 tracker_log.log 确认两个项目线程启动
- [ ] 7.4 2026-11-15 00:00 后确认 data/linglan.csv 首行；2026-11-16 00:00 后确认 icey2 停止
- [ ] 7.5 用户确认后提交并推送；切换完成后再执行 openspec archive

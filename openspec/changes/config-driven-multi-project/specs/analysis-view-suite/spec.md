# analysis-view-suite Specification

## MODIFIED Requirements

### Requirement: 终局与阶段 MUST 给出外推区间与关口 ETA

「终局与阶段」视图 MUST 从当前项目的 campaign 配置读取截止时刻、时间轴起点、阶段列表与里程碑步长，MUST NOT 复用其他项目的配置。视图 MUST 用最近 7 个完整自然日的日增金额分别做等差外推（每日增量取 7 日均值）与对数线性拟合（指数衰减），外推到该项目配置的截止时刻，并显示两条轨迹及其包络区间。仅当配置提供 phases 时，MUST 在累计曲线上标注阶段分界与各阶段日均金额；未提供 phases 时 MUST NOT 绘制灰带、阶段标注或阶段日均。图表 MUST 按当前最大总金额自适应计算里程碑步长：取 max(总金额)/40 最接近的 {1, 2, 2.5, 5}×10^n（n 为非负整数，步长不小于 1 元）；campaign.milestone_step 显式配置时 MUST 使用该值。金额显示单位 MUST 随量级在 元、万元、百万元 之间切换；ICEY2 的百万元刻度与既有 27M/30M/10M 文本 MUST 保持不变。MUST 标注下一个整步长关口的预计到达日期。当数据末行晚于截止时刻时 MUST 停止外推。

#### Scenario: 正常外推
- **WHEN** 数据末行早于项目截止时刻，且最近 7 个完整日都有日增
- **THEN** 显示两条外推轨迹、区间包络与截止时刻的预计总额

#### Scenario: 未配置阶段
- **WHEN** 当前项目的 campaign 没有 phases
- **THEN** 视图仍绘制日增柱状图、3 日均线、累计曲线与外推，但不绘制灰带与阶段日均标注

#### Scenario: 关口 ETA
- **WHEN** 当前累计金额位于两个整步长关口之间
- **THEN** 标注下一关口的预计日期，并分别按包络下限与上限给出区间

#### Scenario: 小额项目自适应
- **WHEN** 当前项目最大总金额为 3,781 元且未配置 milestone_step
- **THEN** 里程碑步长为 100 元，金额以元显示，不出现 0M 标注

#### Scenario: 显式覆盖步长
- **WHEN** campaign.milestone_step 为 5000
- **THEN** 里程碑与下一关口均按 5000 元计算

#### Scenario: 已过截止时刻
- **WHEN** 数据末行时间晚于项目配置的截止时刻
- **THEN** 不绘制外推轨迹，只显示实际累计曲线；配置了 phases 时同时显示阶段标注

## ADDED Requirements

### Requirement: 分析窗口 MUST 按项目配置加载数据

图表启动时 MUST 通过项目名选择 trackers.json 中的项目；命令行未指定时 MUST 使用 defaults.project，未配置 defaults.project 时 MUST 使用文件中第一个项目。图表 MUST 从该项目的 data_file 读取 CSV，MUST NOT 使用写死的文件名。图表 MUST 支持 Lv.{n} 与 Lv{n} 两种列名写法，MUST NOT 假定档位数为 9，档位数量 MUST 以实际表头为准。

#### Scenario: 五档项目
- **WHEN** 选择只包含 Lv.1 到 Lv.5 与无偿支持的项目
- **THEN** 十个视图都只渲染这六个档位，不出现空档位或缺色告警

#### Scenario: 未指定项目
- **WHEN** 配置声明 defaults.project 为 icey2，命令行未传项目名
- **THEN** 图表加载 icey2 的 data_file

#### Scenario: 旧列名兼容
- **WHEN** 表头仍是 Lv1（￥42） 这种旧写法
- **THEN** 图表正常识别档位序号与价格

### Requirement: 档位序列 MUST 按价格升序且以价格识别

图表 MUST 从列名末端解析价格，并按价格升序排列付费档位；价格相同时 MUST 按 Lv 序号升序。图例与堆积顺序 MUST 使用解析后的档位序列，MUST NOT 依赖列在 CSV 中的物理顺序。表头出现同一价格的多个档位时，MUST 按列名中的 Lv 序号配对，MUST NOT 合并或丢弃重复价格列。

#### Scenario: 档位顺序被打乱
- **WHEN** CSV 中 Lv.2 列写在 Lv.1 之前
- **THEN** 图表仍按价格从低到高排列档位

#### Scenario: 标题变更后的列
- **WHEN** 表头中某档位列名保持旧标题，但价格未变
- **THEN** 图表仍把该列视为同一档位，不新增系列

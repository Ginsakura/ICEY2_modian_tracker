# tracker-config Specification

## ADDED Requirements

### Requirement: 配置 MUST 使用单一 JSON 文件描述项目集合

系统 MUST 从仓库根目录的 trackers.json 读取配置。文件 MUST 包含 version 与 projects；version MUST 等于 1；projects MUST 为对象，键为项目名，值为项目条目；defaults 为可选对象，用于提供项目级默认值。未声明 defaults.interval 时，项目抓取间隔 MUST 默认为 60 秒。

#### Scenario: 最小配置可以启动
- **WHEN** trackers.json 的 version 为 1，projects 中只有一个项目且未写 interval
- **THEN** 采集端以 60 秒间隔抓取该项目

#### Scenario: 版本不支持
- **WHEN** trackers.json 的 version 为 2
- **THEN** 配置加载失败并给出中文错误，采集端不启动任何项目

### Requirement: 项目条目 MUST 通过统一校验

项目名 MUST 匹配 ^[A-Za-z0-9_]{1,32}$，且 MUST 大小写不敏感唯一。每个项目 MUST 声明 url 与 data_file；url MUST 指向 zhongchou.modian.com 的 /item/<数字>.html；data_file MUST 是以 .csv 结尾的相对路径，MUST NOT 为绝对路径，MUST NOT 包含 ..；interval 若出现 MUST 是 30 到 3600 之间的整数；enabled 若出现 MUST 是布尔值，缺省视为 true；collect_from 与 collect_until 若出现 MUST 为 YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS 格式的本地时间，且两者都出现时 collect_from MUST 早于 collect_until。项目条目出现未定义字段时 MUST 校验失败，MUST NOT 静默忽略。不同项目的 data_file MUST NOT 解析到同一文件，Windows 下 MUST 按大小写不敏感比较。

#### Scenario: 项目名大小写冲突
- **WHEN** projects 同时包含 Icey2 与 icey2
- **THEN** 校验失败，错误信息同时指出两个冲突的项目名

#### Scenario: 数据文件越界
- **WHEN** data_file 为 ../outside.csv 或 D:/data.csv
- **THEN** 校验失败，且不创建或改写任何文件

#### Scenario: 非摩点链接
- **WHEN** url 的主机不是 zhongchou.modian.com
- **THEN** 校验失败并说明当前只支持摩点项目

#### Scenario: 两个项目指向同一文件
- **WHEN** 两个项目的 data_file 都解析为 data/shared.csv（或仅大小写不同）
- **THEN** 校验失败，并指出两个项目名与冲突路径

### Requirement: campaign 字段 MUST 可选且按项目隔离

每个项目的 campaign 为可选对象，包含 deadline、start、phases、milestone_step。deadline 与 start 若出现 MUST 为 YYYY-MM-DD HH:MM 格式的本地时间；phases 若出现 MUST 为非空数组，每项包含非空 name、start、end，日期格式为 YYYY-MM-DD，且 start 必须早于 end，区间为左闭右开；milestone_step 若出现 MUST 为正整数，作为自适应步长的显式覆盖；未配置时图表 MUST 按数据计算步长，MUST NOT 使用固定默认值。项目之间 MUST NOT 共享或继承 campaign 字段。

#### Scenario: 未配置 campaign
- **WHEN** 某项目条目没有 campaign
- **THEN** 该项目正常采集，图表不绘制截止线、阶段灰带与阶段日均；里程碑仍按数据自适应显示

#### Scenario: 阶段区间非法
- **WHEN** phases 中某项的 start 不早于 end
- **THEN** 校验失败并指出该项目与阶段序号

### Requirement: 采集窗口 MUST 可选且按项目生效

每个项目 MAY 声明 collect_from 与 collect_until，格式 MUST 为 YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS 的本地时间；两者都出现时 collect_from MUST 早于 collect_until；只出现其中一个时 MUST 视为单边窗口。采集端 MUST 在 collect_from 之前不发起任何请求，MUST 在 collect_until 之后停止采样，collect_until 为开区间。窗口不属于 campaign，MUST NOT 用 campaign.deadline 替代采集起止。

#### Scenario: 延迟启动
- **WHEN** 项目配置 collect_from 为未来时刻
- **THEN** 采集线程等待到该时刻后才发起第一次请求

#### Scenario: 窗口已结束
- **WHEN** 启动时当前时间已晚于 collect_until
- **THEN** 该项目不发起请求，线程直接结束并记录 skipped

#### Scenario: 窗口顺序非法
- **WHEN** collect_from 晚于或等于 collect_until
- **THEN** 配置校验失败并指出该项目

### Requirement: 配置工具 MUST 同时提供交互与非交互入口

系统 MUST 提供 manage.py：不带子命令时进入交互式菜单，至少支持列出项目、新增项目、修改项目、停用项目与预览；带子命令时 MUST 支持 list、check、preview、add、disable。所有子命令的校验结果与错误信息 MUST 使用简体中文，check 在配置非法时 MUST 以非零退出码结束。

#### Scenario: 交互式新增项目
- **WHEN** 用户在菜单中新增一个项目并依次填写项目名、追踪链接、数据文件名
- **THEN** 工具在保存前展示预览，用户确认后才写入 trackers.json

#### Scenario: 非交互校验失败
- **WHEN** 执行 manage.py check 且配置中存在重复项目名
- **THEN** 命令以非零退出码结束，并输出冲突的项目名

### Requirement: 保存前 MUST 用真实抓取预览

交互式新增、修改与 preview 子命令 MUST 使用与采集端相同的解析逻辑实际抓取一次目标页面，并展示时间戳、总人数、总金额、档位数量与将要写入的档位列名。抓取失败或解析不到任何档位时 MUST 中止保存并报告原因，MUST NOT 写入配置；用户确认预览结果后才 MUST 允许保存。

#### Scenario: 预览成功
- **WHEN** 用户对一个摩点项目执行 preview
- **THEN** 输出一行时间戳、总人数、总金额，并逐行列出 Lv 列名与份数

#### Scenario: 预览失败不落盘
- **WHEN** 目标页面请求超时或返回非 200
- **THEN** 工具报告失败原因，trackers.json 的修改时间与内容保持不变

### Requirement: 配置写入 MUST 原子且保留未管理字段

保存配置时 MUST 先写同目录临时文件，再原子替换 trackers.json；写入失败时 MUST 保持原文件不变。配置工具 MUST 原样保留它不编辑的字段，包括 campaign.phases、campaign.milestone_step、collect_from/collect_until 以及 defaults 中的未知但合法的内容；MUST NOT 因保存而删除这些字段。

#### Scenario: 保存失败
- **WHEN** 临时文件写入过程中发生磁盘错误
- **THEN** 原 trackers.json 内容不变，工具报告错误并以非零退出码结束

#### Scenario: 手工维护的 phases 被保留
- **WHEN** 用户用配置工具修改某项目的 interval，而该项目已手工配置 phases
- **THEN** 保存后的 phases 内容与修改前完全一致

### Requirement: 默认项目 MUST 可确定

defaults.project 若出现 MUST 指向一个已存在的项目名；未配置时，默认项目 MUST 为 projects 中的第一个项目。图表与预览类命令在未显式指定项目时 MUST 使用默认项目。

#### Scenario: 默认项目不存在
- **WHEN** defaults.project 指向配置中不存在的项目
- **THEN** 配置校验失败并指出该名称

#### Scenario: 未指定项目时取第一个
- **WHEN** 配置未声明 defaults.project，图表命令未传项目名
- **THEN** 使用 projects 中书写顺序的第一个项目

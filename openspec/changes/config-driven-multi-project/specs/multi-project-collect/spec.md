# multi-project-collect Specification

## ADDED Requirements

### Requirement: 采集进程 MUST 为每个启用项目启动独立线程

采集进程启动时 MUST 只加载一次 trackers.json，并仅为 enabled 为 true 的项目启动采集线程；每个项目 MUST 拥有独立的采集循环与独立的数据文件。进程内 MUST 只启动一个 git 同步线程，其调度继续由 sync_config.json 决定。

#### Scenario: 多项目同时采集
- **WHEN** 配置中有两个启用项目，间隔分别为 60 秒与 300 秒
- **THEN** 两个项目按各自间隔持续采集，互不等待

#### Scenario: 停用的项目不抓取
- **WHEN** 某项目 enabled 为 false
- **THEN** 进程不为其创建采集线程，也不发起任何页面请求

### Requirement: 单项目失败 MUST NOT 影响其他项目

某个项目的抓取、解析或写盘失败时，采集进程 MUST 记录包含项目名的错误日志，并在下一个间隔继续重试该项目；MUST NOT 因此终止其他项目的采集线程，也 MUST NOT 退出整个进程。

#### Scenario: 单个项目请求超时
- **WHEN** 项目 A 的页面请求超时，项目 B 正常
- **THEN** 日志记录项目 A 的超时错误，项目 B 继续按自己的间隔采集

#### Scenario: 单个项目表头重写失败
- **WHEN** 项目 A 的 CSV 重写失败
- **THEN** 项目 A 的旧文件保持不变，项目 B 的追加不受影响

### Requirement: 抓取间隔 MUST 按项目独立计算

同一项目相邻两次抓取的开始时间间隔 MUST 不小于该项目配置的 interval；若单次抓取耗时已超过 interval，MUST 立即开始下一轮而不叠加等待。不同项目之间 MUST NOT 相互限制。

#### Scenario: 抓取耗时超过间隔
- **WHEN** 某项目一次抓取耗时 70 秒而 interval 为 60 秒
- **THEN** 本轮结束后立即开始下一轮，不再额外等待 60 秒

#### Scenario: 默认间隔
- **WHEN** 项目未配置 interval 且 defaults 也未配置
- **THEN** 该项目按 60 秒间隔抓取

### Requirement: 采集窗口 MUST 限制采样时段

采集线程 MUST 在 collect_from 之前保持等待且不发起任何请求，等待 MUST 可被 SIGINT/SIGTERM 立即打断；collect_from 到达后 MUST 立即开始第一轮。collect_until 为开区间：下一次计划采样时刻大于或等于 collect_until 时该线程 MUST 结束，启动时若当前时间已晚于 collect_until 则 MUST 不发起请求直接结束。所有采集线程都因窗口结束而退出后，进程 MUST 停止同步线程、删除 run.pid 并退出。--once MUST 遵循同样的窗口规则。manage.py 的 preview MUST NOT 受采集窗口限制。

#### Scenario: 启动时尚未到点
- **WHEN** 项目配置的 collect_from 在 5 分钟后
- **THEN** 线程等待期间不发起请求，到点后立刻采集第一轮

#### Scenario: 运行中到达终点
- **WHEN** 某项目的 collect_until 已到
- **THEN** 该线程完成当前轮后结束，不再发起下一轮；其他项目继续采集

#### Scenario: 全部项目结束
- **WHEN** 所有启用项目的窗口都已结束
- **THEN** 进程删除 run.pid 后退出，run state 不再显示运行中

### Requirement: 运行期 MUST NOT 热加载配置

采集进程 MUST 只在启动时读取 trackers.json；运行期间修改配置 MUST NOT 改变当前进程的项目集合或间隔。新配置 MUST 在停止并重新启动进程后生效。

#### Scenario: 运行中新增项目
- **WHEN** 进程运行期间向 trackers.json 新增一个项目
- **THEN** 当前进程不开始抓取该项目，重启后才会启动其采集线程

#### Scenario: 运行中修改间隔
- **WHEN** 进程运行期间把某项目 interval 从 60 改为 120
- **THEN** 当前进程仍按 60 秒采集，重启后改为 120 秒

### Requirement: 试运行 MUST 只采集一轮并汇总结果

采集入口 MUST 支持 --once：加载配置后按每个启用项目的采集窗口处理，窗口内的项目各抓取一轮并写入数据文件，窗口外的项目标记为 skipped 且不发起请求；打印包含项目名、状态、总人数、总金额的汇总后退出。--once MUST NOT 启动 git 同步线程，也 MUST NOT 写 run.pid。没有抓取失败时退出码 MUST 为 0（全部 skipped 也算成功），任一项目抓取或写入失败时 MUST 为非零。

#### Scenario: 全部成功
- **WHEN** 两个启用项目都在采集窗口内且抓取写入成功
- **THEN** 输出两行成功汇总并以 0 退出

#### Scenario: 窗口外跳过
- **WHEN** 一个项目已过 collect_until，另一个项目在窗口内
- **THEN** 过期项目标记 skipped 且不发起请求，另一个项目正常写入，退出码为 0

#### Scenario: 部分失败
- **WHEN** 一个项目成功、另一个解析失败
- **THEN** 成功项目的数据已写入，汇总标出失败项目，退出码非零

### Requirement: 进程 MUST 支持可控启停与完整退出

采集进程 MUST 把自身 PID 写入仓库根目录的 run.pid，退出时 MUST 删除该文件。启动脚本 MUST 继续支持 start、stop、restart、state 四个动作；stop MUST 结束整个采集进程，restart MUST 在停止后按最新配置重新启动。进程收到 SIGINT 或 SIGTERM 时 MUST 在完成当前轮写入后停止所有线程并退出，MUST NOT 留下未写完的数据行。所有采集线程因采集窗口结束而自然退出后，进程 MUST 删除 run.pid 并退出。

#### Scenario: 正常停止
- **WHEN** 执行启动脚本的 stop 动作
- **THEN** 采集进程退出，run.pid 被删除

#### Scenario: 进程被强制结束
- **WHEN** 采集进程被 SIGKILL 结束
- **THEN** 下次追加前按 CSV 规则修复可能残留的半行，run.pid 在下次启动时被覆盖

### Requirement: 日志 MUST 可区分项目且并发安全

采集进程写出的每条日志 MUST 包含来源模块或项目名；多个线程并发写同一日志文件时 MUST 保证单行完整，MUST NOT 出现两条日志字节交错。日志文件路径 MUST 与既有 tracker_log.log 保持一致。

#### Scenario: 两个项目同时记录
- **WHEN** 两个采集线程在同一秒内各写一行日志
- **THEN** 文件中的两行各自完整，且都带项目名

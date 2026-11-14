# 设计：配置驱动的多项目追踪器

## 背景

当前实现围绕单个众筹项目写死：modian_tracker.py:25 的 URL、:26 的 CSV_FILE、:305 起的 main 只接受 interval；charts.py:44 的 CSV_FILE、:49-50 的里程碑步长、:66-72 的阶段、:73 的截止时间、:76 的时间轴左边界；run 与 run.ps1 各自维护 PID、日志与启动参数；shared_logger.py:6 固定单个日志文件。

现有数据与运行方式构成硬约束：

- data.csv 为 UTF-8 BOM + CRLF、55,597 行、13 列，表头为 timestamp、总人数、总金额、无偿支持、Lv1（￥42）…Lv9（￥598）。
- 两个项目的档位数不同：160465 为 9 个付费档，160539 为 5 个付费档；两者价格、标题、截止时间均不同。
- 运行环境同时有 Windows PowerShell 与 Termux bash；Termux 没有 jq，shell 不适合解析 JSON。
- 每日 git 同步由 sync_data.py 的线程完成，提交命令是 git add . 后 commit、push，因此新增的 data/ 目录会自然纳入提交范围。
- 分析端有十个视图与两套验证脚本，改动必须保持既有视图行为。

## 目标 / 非目标

**目标：**

- 用仓库根的 trackers.json 描述项目集合，新增项目只改配置。
- 单进程内为每个启用项目跑一个采集线程，另有一个全局 git 同步线程。
- 提供 manage.py：交互菜单 + 非交互子命令，保存前用真实抓取预览。
- 图表按项目加载 CSV 与 campaign 元数据；phases 可选，默认不画。
- 档位列名带原始标题；价格作为档位身份，标题变更不新增列。
- 把 icey2 数据文件移动为 data/icey2.csv，与 data/linglan.csv 统一布局；旧表头不迁移。

**非目标：**

- 不支持摩点以外的平台；不引入站点/解析器插件机制。
- 不做 Web 界面、不做热加载、不做多进程或独立 launcher 进程。
- 不回填 linglan 的历史数据（从接入时刻开始采集）。
- 不改变十个视图的统计口径；不重构图表布局。
- 不自动迁移旧表头，也不在代码里保留旧版单项目入口；旧表头仅在追加/绘图时按价格兼容。

## 关键决策

### 1. 配置模块与文件布局

新增三个文件：

- trackers.json：唯一配置源。
- tracker_config.py：加载、校验、默认项目解析、原子保存，以及 CampaignConfig 的日期解析。
- tier_columns.py：列名生成、标题归一化、从列名末端解析价格。
- manage.py：配置工具。

配置解析放在 Python 而不是 shell，原因是 Termux 没有 jq，而且校验、预览、原子写都需要 Python。run 与 run.ps1 只负责进程管理，不再解析配置。

trackers.json 采用如下形态（milestone_step 是可选的显式覆盖，不写则按数据自适应）：

    {
      "version": 1,
      "defaults": { "interval": 60, "project": "icey2" },
      "projects": {
        "icey2": {
          "url": "https://zhongchou.modian.com/item/160465.html",
          "data_file": "data/icey2.csv",
          "interval": 60,
          "enabled": true,
          "collect_until": "2026-11-16 00:00",
          "campaign": {
            "deadline": "2026-11-15 23:59",
            "start": "2026-10-05 22:00",
            "phases": [
              { "name": "P1 起势", "start": "2026-10-05", "end": "2026-10-08" },
              { "name": "P2 退坡", "start": "2026-10-08", "end": "2026-10-15" },
              { "name": "P3 二次脉冲", "start": "2026-10-15", "end": "2026-10-18" },
              { "name": "P4 断崖", "start": "2026-10-18", "end": "2026-10-23" },
              { "name": "P5 长尾", "start": "2026-10-23", "end": "2026-11-16" }
            ]
          }
        },
        "linglan": {
          "url": "https://zhongchou.modian.com/item/160539.html",
          "data_file": "data/linglan.csv",
          "interval": 300,
          "enabled": true,
          "collect_from": "2026-11-15 00:00",
          "collect_until": "2027-01-10 12:00",
          "campaign": {
            "deadline": "2027-01-10 12:00"
          }
        }
      }
    }

data_file 相对 trackers.json 所在目录解析，校验时拒绝绝对路径与 ..，并新增一条校验：两个项目的 data_file 解析后不得指向同一文件（Windows 下按大小写不敏感比较），否则并发追加会互相破坏。collect_from 与 collect_until 是可选的采集窗口，使用本机本地时间（Asia/Shanghai）；collect_until 为开区间，到点后不再采样。milestone_step 只在需要钉死步长时手工写入，配置向导不提供该字段。

### 2. 单进程多线程

主线程解析参数、加载配置、写 run.pid、启动线程，然后用 stop Event 等待退出信号。每个启用项目一个 Collector 线程；全局只有一个同步线程，直接复用 sync_data.sync_loop，不改变 sync_config.json。

线程循环用单调时钟计算下一次开始时刻，next_at += interval，睡眠用 stop.wait(max(0, next_at - now))，这样 SIGINT/SIGTERM 能立即唤醒而不是睡满一个间隔。单次抓取耗时超过 interval 时下一轮立即开始，不叠加等待。

#### 采集窗口

每个项目可有可无 collect_from 与 collect_until，使用本地时间 YYYY-MM-DD HH:MM[:SS]，且 collect_from 必须早于 collect_until。采集线程在 collect_from 之前不发起请求，用 stop.wait 以不超过 60 秒的分片等待，因此 SIGINT/SIGTERM 能立即打断；到点后立即采第一行。collect_until 为开区间：下一次计划时刻 >= collect_until 时线程结束，启动时若已过 collect_until 则不抓取直接结束。

--once 同样尊重窗口：窗口外的项目标记 skipped 且不发起请求，skipped 不算失败；manage.py preview 不受窗口限制。所有采集线程都结束后，主线程停止同步线程、删除 run.pid 并退出。campaign.deadline 仍然只用于图表外推与截止线，不承担采集起止语义。

选择单进程而不是“launcher 拉起多个 Python 实例”：

- 一个 run.pid、一个日志文件、一个 git 同步线程，启停语义简单。
- 多个进程会各自持有 sync 线程，git add/commit 互相竞争。
- 抓取是网络 IO，解析是轻量 bs4，GIL 不构成瓶颈。
- 配置校验只需做一次，项目间共享同一个配置快照。

不选择 asyncio：现有 requests + BeautifulSoup 同步代码可原样复用，改造面最小。

### 3. 档位身份与列名

解析页面时把每个付费档整理为 Tier(price_text, price_value, title, count)。价格用 Decimal 比较，避免 45.6 这类浮点误差；价格文本保留页面原样，用于列名。

列名生成（tier_columns.py）：

1. 取 .back-sub-title 文本并去首尾空白。
2. 用正则 ^LV\s*\d+[\s·\-—:：]* 去掉开头的编号与分隔符（大小写不敏感）。
3. 若剩余为空，写 Lv.{n}（￥{price}）；否则写 Lv.{n} {title}（￥{price}）。
4. 免费档固定为 无偿支持。

示例：LV1 早鸟 数字版 → Lv.1 早鸟 数字版（￥42）；LV3 → Lv.3（￥88）；吧唧也想贴贴 → Lv.3 吧唧也想贴贴（￥38）。

绑定算法 resolve_columns(header, tiers)：

- 先把表头解析为 free 列与付费列序列，每个付费列带价格。
- 对本次每个付费档，按价格找到同价列列表，取该价格第 k 次出现对应的第 k 个列；k 用出现次数计数。
- 找到则沿用旧列名（即使标题已变）；找不到则生成新列名，标记需要重写表头。
- 免费档沿用 无偿支持，不存在则新增。

追加路径只读表头，缓存每个项目的表头直到发生重写；重写后更新缓存。若需要新增列，使用 _rewrite_tiers 的原子替换逻辑，历史行补空字符串；旧表头 Lv1（￥42）按价格继续绑定，不强制改名。价格解析统一用列名末端的全角括号，正则锚定结尾，避免标题中的 ￥19.9 被误认。

### 4. 图表上下文

新增 ChartContext 数据类，从 ProjectConfig 构造：csv_file、deadline、plot_left、phases、milestone_step（可选覆盖）、project_key。里程碑步长默认按当前最大总金额自适应：

    step = nice_nearest(max(总金额) / 40)
    nice = {1, 2, 2.5, 5} × 10^n，n >= 0，最小 1 元

icey2 的 max/40 约为 1,041,679，取 1,000,000，因此 27M/30M/10M 文本不变；linglan 的 3,781/40 约为 94.5，取 100，已经越过的 100…3,700 关口没有历史点可判定，会从 3,800 元起出现。campaign.milestone_step 显式配置时覆盖自动值，配置向导不提供该字段。major_step 仍为 10 × step。

金额显示单位随量级切换：最大总金额达到百万元级（>=1e6）时用百万元（1e6），万元级（>=1e4）用万元（1e4），更小用元（1）。抽出 fmt_amount、fmt_axis 与 unit_label，替换 charts.py:903、908、1762、1778、1781、1794、1796、1802、2119、2121、2132 处写死的 /1e6、百万元与 :0fM；右轴刻度在百万元模式下保持 0.5/0.1 以维持 icey2 逐像素一致，其余单位按数据范围生成。自适应步长与单位每次渲染都按当时数据重算，跨量级后同一张图的关口线会变化，这是可接受的取舍。

AnalyzeChart 与 PlotChart 构造签名改为 (csv_file=None, project=None, logger=None, refresh_interval=60)。解析顺序：显式 csv_file 优先于 project.data_file；project 缺省时取 defaults.project，再缺省取第一个项目。这样 scripts/render_preview.py 与 scripts/verify_views.py 传临时 CSV 仍可工作，只是 campaign 元数据来自默认项目。

替换以下模块级常量引用：charts.py:49-50 的 MILLION_STEP/TENMILLION_STEP、:66-72 的 CAMPAIGN_PHASES、:73 的 DEADLINE、:76 的 PLOT_X_LEFT，以及 :559、:574、:779、:819、:939、:941、:969、:1762、:1778、:1796、:1802、:2079、:2110、:2132 的使用点（金额格式化与单位相关代码按上一段处理）。

列解析：_lv_no 改为 Lv\.?\s*(\d+)；tier_prices 使用末端价格正则并按 (price, lv) 排序；charts.py:1819 的横条图标签压缩正则同步改写，输出 Lv.3 ￥38 形态。旧表头 Lv1（￥42）仍能解析。

phases 为空时，两处 axvspan 与阶段标注循环直接跳过；deadline 为空时 finale_forecast 返回未外推原因，视图只画实际曲线，不画截止线与外推；plot_left 为空时用数据首行时间。

图例宽度是本设计的主要视觉风险：标题列名比原来的 Lv1（￥42）长，9 档时图例可能变宽。处理顺序：先按现状渲染并检查；若溢出，再只对图例标签做标题截断（保留 CSV 与提示框全名），不改列名。

### 5. 配置工具

manage.py 不带子命令时进入菜单：1 列表、2 新增、3 修改、4 停用、5 预览、0 退出。非交互子命令为 list、check、preview、add、disable。

保存流程固定为：读取原始 JSON 字典 → 校验 → 修改目标字段 → 再次校验 → 真实抓取预览 → 用户确认 → 写同目录临时文件 → os.replace。只修改用户编辑的键，phases、milestone_step 等未管理字段原样保留。

preview 使用与采集端相同的 fetch 与 parse 函数，输出时间戳、总人数、总金额、档位数量与生成的列名；抓取失败、非 200、解析不到档位时中止保存。Windows 控制台默认 GBK，manage.py 启动时把 stdout/stderr reconfigure 为 UTF-8，避免打印全角￥时报 UnicodeEncodeError。

### 6. 启停与 PID

run.pid 的所有权移到 Python：采集进程启动时写入 os.getpid()，正常退出时删除；--once 不写。启动脚本 start 后最多等待 2 秒确认 run.pid 出现，stop 读取 PID 并结束进程树，state 检查 PID 是否存在，restart 为 stop 后 start。

run 与 run.ps1 不再接受 interval 参数；多传参数时直接报用法错误，抓取间隔只由 trackers.json 决定。

### 7. 日志

shared_logger.get_logger(name) 的格式加入 name，采集线程使用 collector:<project>，同步线程沿用 sync_data。logging 的 Handler 自带锁，单行不会交错；日志路径仍为 tracker_log.log。

## 风险 / 权衡

- 标题被作者修改：价格身份保证不新增列，但表头保留旧标题，显示会过期。接受，因为改列名会让历史行与图表标签一起变动。
- 同价档位：按出现顺序绑定，顺序变化会导致错位。摩点同价档位少见，接受并在校验/日志中提示同价列数量。
- 图表常量替换面大：用 scripts/render_preview.py 在改动前后各渲染一组 PNG 做对照，重点看 icey2 的里程碑、截止线、阶段灰带文本是否完全一致。
- 图例变宽：先实测再决定是否截断，不提前改列名。
- 旧进程仍在写旧文件：必须先在手机端 stop，再移动 data.csv；否则旧进程继续按 data.csv 路径写入，移动后会产生两个互不相关的文件。移动完成后再启动统一采集器。
- 旧表头不迁移：data/icey2.csv 保留 Lv1（￥42）…Lv9（￥598）列名，追加器按价格绑定，历史与新增数据都可正常绘图；新建 CSV 才使用带标题的列名。若后续希望统一显示标题，可在停机状态下手工改首行，不影响价格身份。
- git add . 与追加并发：单行追加是一次小写入，重写用原子替换；风险低。若实测发现 git 提交了半行，再在 append 与 git add 之间加进程锁。
- 自适应步长随总额变化：linglan 跨过 6,000、10,000 等量级时同一张图的关口线与单位会变；数据不受影响，接受截图不可逐像素对比。
- 时区：配置中的 deadline/start/collect_from/collect_until 按本机本地时间解释（Asia/Shanghai），与现有 naive datetime 行为一致；不做时区转换。
- 回滚：停止新进程，把 data/icey2.csv 移回 data.csv，代码回滚到变更前提交，再用旧 run 启动 ICEY2；旧代码不读 trackers.json。

## 上线计划

最终版本直接在手机端切换，不保留桥接、迁移脚本或旧 API：

1. 本机用临时配置与临时 CSV 验证：配置校验、价格身份追加、十视图回归、窗口测试、--once。真实 data.csv 不写入。
2. 停止手机上的旧采集进程（./run stop）；确认没有旧进程继续写 data.csv。
3. 拉取最终代码，执行 mkdir -p data && mv data.csv data/icey2.csv；确认 trackers.json 中 icey2 的 data_file 是 data/icey2.csv。
4. 启动统一采集器（./run start）。ICEY2 采集到 2026-11-16 00:00；linglan 等待到 2026-11-15 00:00 后每 300 秒采样。
5. 用 ./run state、tracker_log.log 与 --analyze --project icey2|linglan 检查：两个项目各自的 CSV、linglan 首行、档位数、截止线与自适应里程碑。
6. 若 00:00 前尚未完成切换，linglan 会从实际启动时刻开始采集，不会补采；这是可接受的窗口语义。

## 已确认决策

- 里程碑步长自适应：max/40 取最接近的 {1,2,2.5,5}×10^n；campaign.milestone_step 仅作显式覆盖，向导不编辑。
- 采集窗口放在项目级 collect_from/collect_until，本地时间、终点开区间；不复用 campaign.deadline。
- 不保留临时桥接与表头迁移脚本；旧表头由价格身份兼容，不强制改名。
- ICEY2 的 collect_until 设为 2026-11-16 00:00；LINGLAN 的 collect_until 设为 2027-01-10 12:00。
- ICEY2 数据路径为 data/icey2.csv；LINGLAN 为 data/linglan.csv。

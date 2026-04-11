# Real-time GaN Industry Intelligence

一个可运行的 GaN（氮化镓）产业情报系统，支持：

- 多源实时抓取：产业新闻、企业官网线索、学术检索（IEEE/Nature/APL）
- 自动分类：`企业产业 / 股市 / 学术` 与 `低功率 / 高功率 / 高频 / 材料 / 封装`
- 企业白名单源配置：预置 `VLSI / Infineon / onsemi / ST / Navitas / Wolfspeed / TI / Renesas`
- 数据源可视化可编辑：新增、启停、复核、人工确认
- DeepSeek 分析：情绪、影响力、摘要
- Gmail 周报：备用 Gmail 自动发送到工作 Gmail
- 股票折线图数据：支持历史回灌和网页折线展示

## 1. 快速启动

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

打开 `http://127.0.0.1:8787/`

## 2. 必填配置（.env）

至少配置以下字段：

- `DEEPSEEK_API_KEY`
- `GMAIL_USER`
- `GMAIL_APP_PASSWORD`（Gmail App Password，不是登录密码）
- `GMAIL_TO`

可选字段：

- `STOCK_TICKERS_CSV` 自定义追踪股票
- `INGEST_INTERVAL_MINUTES` / `STOCK_INTERVAL_MINUTES` 抓取频率
- `WEEKLY_CRON_*` 周报时间（按 `TIMEZONE`）

## 3. Web 面板功能

- 情报筛选：模块按钮（学术/功率等）/ 一级分类 / 技术分类 / 关键词 / 回看天数 / 日期区间
  - 默认展示全部情报流
  - 切换分类后即时显示对应模块情报流
  - 支持在每条文章卡片中手动调整分类并保存
- 企业白名单配置：
  - 新增企业与官网域名
  - 启用/停用白名单企业
  - 一键同步到来源库
- 来源网站可视化编辑：
  - 模块化查看来源
  - 新增来源
  - 启用/停用
  - 复核与人工确认
- 股票监控：
  - 最新快照表
  - 折线图（按 ticker 与点数）
  - 一键回灌历史折线数据（默认 `5d x 15m`）
- 文章内容补全：
  - 对缺少摘要/正文的文章抓取页面元描述与正文片段
  - 可通过页面按钮“补全概要/正文”触发

## 4. 真实性校验

- URL 可达性检查（HTTP）
- 可信域名白名单匹配
- 状态判定：
  - `verified`：可达且命中可信域名
  - `unverified`：可达但未命中白名单（可人工确认）
  - `failed`：不可达或格式异常
- 抓取任务默认只使用 `verified` 或已人工确认来源

## 5. API 端点

通用数据：

- `GET /api/articles`
- `GET /api/stats`
- `GET /api/stocks`
- `GET /api/stocks/{ticker}/series`

来源管理：

- `GET /api/source-sites/modules`
- `GET /api/source-sites`
- `POST /api/source-sites`
- `PUT /api/source-sites/{id}`
- `POST /api/source-sites/{id}/verify`
- `POST /api/source-sites/{id}/approve`

企业白名单：

- `GET /api/company-whitelist`
- `POST /api/company-whitelist`
- `PUT /api/company-whitelist/{id}`
- `POST /api/company-whitelist/sync`

任务触发：

- `POST /api/tasks/ingest`
- `POST /api/tasks/stocks`
- `POST /api/tasks/stocks/backfill`
- `POST /api/tasks/enrich-content`
- `POST /api/tasks/send-weekly`

## 6. 数据源说明

- 通用产业源：Google News RSS 检索
- 学术源：arXiv API + Crossref（Nature/APL/IEEE 方向）
- 企业白名单源：自动根据企业官网域名生成 `site:domain` 的 RSS 检索

> IEEE Xplore、Nature 正文等可能涉及访问权限，默认实现使用公开检索与摘要信息。

## 7. 项目结构

```text
app/
  api.py
  config.py
  db.py
  models.py
  sources.py
  source_registry.py
  taxonomy.py
  deepseek_client.py
  pipeline.py
  reporter.py
  scheduler.py
  templates/index.html
main.py
```

## 8. 生产部署建议

- 用 PostgreSQL 替代 SQLite（`DB_URL`）
- 给管理 API 加鉴权（API Key/OAuth）
- 抓取链路加失败告警与重试策略
- 将企业白名单与来源权限拆分角色管理

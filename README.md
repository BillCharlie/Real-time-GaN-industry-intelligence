# GaN Industry Monitor Assistant — GaN 半導體產業即時情報系統（請務必閱讀 License）
# GaN Industry Monitor Assistant — Real-Time GaN Semiconductor Industry Intelligence
# https://billcharlie.github.io/Real-time-GaN-industry-intelligence/

> **作者 / Author:** Ping yu-Chen, Taiwan
> **版本 / Version:** v2.0
> **授權 / License:** 請詳閱 `LICENSE` / See `LICENSE` — 商業使用須授權 / Commercial use requires written permission

---

## 目錄 / Table of Contents

- [系統簡介 / Overview](#系統簡介--overview)
- [功能總覽 / Features](#功能總覽--features)
- [環境需求 / Requirements](#環境需求--requirements)
- [安裝 / Installation](#安裝--installation)
- [環境設定 / Configuration](#環境設定--configuration)
- [啟動服務 / Start Services](#啟動服務--start-services)
- [分類系統 / Classification System](#分類系統--classification-system)
- [資料來源 / Data Sources](#資料來源--data-sources)
- [報告系統 / Reporting System](#報告系統--reporting-system)
- [Web 面板操作 / Web Panel](#web-面板操作--web-panel)
- [排程任務 / Scheduled Jobs](#排程任務--scheduled-jobs)
- [主要 API / Key API Endpoints](#主要-api--key-api-endpoints)
- [雲端部署 / Deployment](#雲端部署--deployment)

---

## 系統簡介 / Overview

**中文：**
GaN Industry Monitor Assistant 是針對氮化鎵（GaN）半導體領域設計的全自動產業情報系統。系統同時追蹤產業新聞、學術論文（IEEE / Nature / arXiv）與企業官網動態，透過雙階段分類引擎（確定性規則 + DeepSeek LLM）對每篇文章進行產業分類、情緒評分（-1 ～ +1）與影響力評分（0 ～ 100），並以中文每日 / 週報 / 月報形式發送 Email 情報摘要。

**English:**
GaN Industry Monitor Assistant is a fully automated intelligence system for the gallium nitride (GaN) semiconductor industry. The system simultaneously monitors industry news, academic publications (IEEE/Nature/arXiv), and company websites. A dual-stage classification engine (deterministic rules + DeepSeek LLM) categorizes each article by industry type, assigning sentiment scores (-1 to +1) and impact scores (0 to 100). Intelligence summaries are delivered by email as daily/weekly/monthly Chinese-language reports.

---

## 功能總覽 / Features

**中文：**
- **多源同步抓取** — 25 個預設來源：Google News RSS（4 個 GaN 專屬查詢）、arXiv API、Crossref REST（7 本期刊）、IEEE Xplore RSS（4 個 TOC Feed）、Nature / ScienceDirect RSS，支援隨時新增自訂來源
- **GaN 相關性過濾** — 雙層關鍵詞過濾：包含詞（gallium nitride / 氮化鎵 / GaN + 半導體語境詞），排除詞（generative adversarial / large language model 等 AI 同音詞）
- **雙階段分類引擎** — Stage 1 確定性規則分類（離線、零 API）；Stage 2 DeepSeek LLM 精細分類 + 中文摘要（≤120 字）
- **情緒 / 影響力評分** — 每篇文章獲得 sentiment_score（-1 ～ +1）與 impact_score（0 ～ 100）
- **企業白名單自動建源** — 新增企業後自動生成 `site:domain GaN` Google News RSS 來源
- **來源驗證系統** — 49 個可信域名白名單 + HTTP 可達性檢查，三段式狀態（verified / unverified / failed）
- **三級中文報告** — 每日（100–150 字）/ 每週（200–280 字）/ 每月（350–450 字）DeepSeek 分析摘要，Email 發送
- **股票快照追蹤** — 追蹤 GaN 相關股票（NVTS、ON、STM、IFNNY、WOLF 等）即時快照與歷史折線
- **分類層級管理** — 可在 Web 面板新增、啟停、重排分類樹；文章支援手動調整分類

**English:**
- **Multi-source concurrent scraping** — 25 default sources: Google News RSS (4 GaN-specific queries), arXiv API, Crossref REST (7 journals), IEEE Xplore RSS (4 TOC feeds), Nature/ScienceDirect RSS; add custom sources at any time
- **GaN relevance filtering** — Two-layer keyword gate: inclusion terms (gallium nitride / 氮化鎵 / GaN + semiconductor context), exclusion terms (generative adversarial / large language model, etc.)
- **Dual-stage classification engine** — Stage 1: deterministic rule engine (offline, zero API calls); Stage 2: DeepSeek LLM refined classification + Chinese summary (≤120 words)
- **Sentiment / impact scoring** — Each article receives sentiment_score (-1 to +1) and impact_score (0 to 100)
- **Company whitelist auto-source generation** — Adding a company auto-creates a `site:domain GaN` Google News RSS source
- **Source verification system** — 49-domain trusted whitelist + HTTP reachability check; three-state status (verified / unverified / failed)
- **Three-tier Chinese reports** — Daily (100–150 words) / Weekly (200–280 words) / Monthly (350–450 words) DeepSeek analysis summaries delivered by email
- **Stock snapshot tracking** — Real-time snapshots and historical charts for GaN-related tickers (NVTS, ON, STM, IFNNY, WOLF, etc.)
- **Category hierarchy management** — Add, activate/deactivate, and reorder category tree in the Web panel; manual article reclassification supported

---

## 環境需求 / Requirements

| 項目 / Item | 版本 / Version |
|-------------|----------------|
| Python | ≥ 3.10 |
| DeepSeek API Key | deepseek-chat 模型 / deepseek-chat model |
| Gmail App Password | 報告 Email 傳輸 / For report email delivery |

---

## 安裝 / Installation

**Windows PowerShell：**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux：**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 環境設定 / Configuration

複製設定範本 / Copy template:

```powershell
Copy-Item .env.example .env
```

編輯 `.env` / Edit `.env`:

```dotenv
# 核心設定 / Core
PROJECT_NAME=GaNIndustry Monitor-assistant
DB_URL=sqlite:///./data/ganiq.db
TIMEZONE=Asia/Taipei

# 抓取排程 / Ingestion schedule
INGEST_INTERVAL_MINUTES=120      # 每 2 小時抓取一次 / Scrape every 2 hours
MAX_ARTICLES_PER_SOURCE=20

# DeepSeek LLM（分類精細化 + 中文摘要 / for classification refinement + Chinese summaries）
DEEPSEEK_ENABLED=true
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_TIMEOUT_SECONDS=35

# Gmail SMTP（報告 Email / for report emails）
GMAIL_USER=your_backup_gmail@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password  # App Password，非登入密碼 / App Password, not login password
GMAIL_TO=your_work_gmail@gmail.com
GMAIL_FROM_DISPLAY_NAME=GaN Intelligence Bot

# 追蹤股票 / Tracked tickers（Yahoo Finance 代碼 / symbols）
STOCK_TICKERS_CSV=NVTS,ON,STM,IFNNY,WOLF,TXN,RNECY,MCHP,ADI

# 週報排程 / Weekly report schedule（預設週一、週四 07:00 / default Mon & Thu 07:00）
WEEKLY_CRON_DAY_OF_WEEK=mon,thu
WEEKLY_CRON_HOUR=7
WEEKLY_CRON_MINUTE=0
```

> Gmail App Password 取得方式：Google 帳戶 → 安全性 → 開啟兩步驟驗證 → 搜尋「應用程式密碼」→ 產生 16 位密碼
> Get Gmail App Password: Google Account → Security → Enable 2-Step Verification → Search "App Passwords" → Generate a 16-character password

---

## 啟動服務 / Start Services

```powershell
python main.py
```

或 / or:

```bash
uvicorn app.api:app --reload --host 0.0.0.0 --port 8001
```

開啟 Web 面板 / Open Web Panel:

```
http://127.0.0.1:8001/
```

> 服務啟動後立即執行一次抓取，之後每 `INGEST_INTERVAL_MINUTES` 分鐘自動執行。
> The system runs an immediate scrape on startup, then repeats every `INGEST_INTERVAL_MINUTES` minutes.

---

## 分類系統 / Classification System

### 分類架構 / Category Structure

情報依「一級分類（宏觀）× 技術分類」雙維度標記：

Articles are tagged by two-dimensional classification: **macro category × tech category**:

**一級分類 / Macro Categories:**

| 鍵 / Key | 說明 / Description |
|----------|---------------------|
| `academic` | 學術論文、會議、預印本 / Academic papers, conferences, preprints |
| `industry` | 產業新聞、設計勝利、出貨、發表 / Industry news, design wins, shipments, announcements |
| `stock` | 財報、法說、投資人新聞 / Earnings, guidance, investor news |

**技術分類 / Tech Categories:**

| 鍵 / Key | 說明 / Description |
|----------|---------------------|
| `low_power` | USB-C 充電器、消費電源（<200W）/ USB-C chargers, consumer power (<200W) |
| `high_power` | 電動車逆變器、電網轉換器、伺服器電源（>3kW）/ EV inverters, grid converters, server PSU (>3kW) |
| `high_frequency` | RF、毫米波、5G/6G、雷達、衛星 / RF, mmWave, 5G/6G, radar, satellite |
| `materials` | 磊晶、基板、晶圓、缺陷 / Epitaxy, substrate, wafer, defects |
| `packaging` | 熱管理、整合、寄生參數 / Thermal management, integration, parasitics |
| `other` | 不符合上述分類 / Does not fit above |

### 雙階段分類流程 / Dual-Stage Classification Pipeline

```
文章標題 + 摘要
      ↓
Stage 1：規則引擎（taxonomy.py）
  - 關鍵詞比對（離線、零延遲）
  - 輸出：(macro, tech, tags[])
      ↓
Stage 2：DeepSeek LLM（deepseek_client.py）
  - JSON 模式（temperature=0.2）
  - 輸出：精細分類 + sentiment_score + impact_score + 中文摘要（≤120字）
      ↓
儲存至 SQLite（articles 表）
```

### GaN 相關性過濾 / GaN Relevance Filter

文章必須通過以下條件才會被收錄 / Articles must pass this gate to be ingested:

**包含（任一）/ Include (any):**
- 完全匹配 `"gallium nitride"` 或 `"氮化鎵"` → 自動通過
- 詞邊界 `\bGaN\b` + 任一語境詞：`semiconductor`, `power electronics`, `hemt`, `gan fet`, `transistor`, `charger`, `inverter`, `mmwave`, `wafer`, `epitaxy`

**排除（任一）/ Exclude (any):**
- `"generative adversarial"`, `"diffusion"`, `"large language model"`, `"machine learning"`, `"image generation"`

---

## 資料來源 / Data Sources

系統預設 25 個資料來源，分三大類：

The system ships with 25 default sources across three categories:

### 新聞類 / News Sources（Google News RSS）

| 名稱 / Name | 查詢策略 / Query Strategy | 時間窗 / Window |
|-------------|--------------------------|-----------------|
| GaN Semiconductor News | `"gallium nitride" OR ("GaN" semiconductor power)` | 7 天 / 7 days |
| GaN Stock & Market | 同上 + 財報關鍵詞 / Same + earnings keywords | 14 天 |
| GaN Fast Charger | USB-C / 充電器角度 / USB-C/charger angle | 14 天 |
| GaN EV Inverter | EV / 電網角度 / EV/grid angle | 30 天 |

### 學術類 / Academic Sources（17 個）

- **arXiv API** — `gallium nitride` + `power electronics` / `high frequency`
- **Crossref API（7 本期刊）** — IEEE TPEL、IEEE EDL、IEEE TED、IEEE Access、Nature Electronics、Nature Energy、APL、ScienceDirect
- **IEEE Xplore RSS（4 個）** — TPEL、EDL、TED、JEDS TOC Feed
- **Nature RSS（3 個）** — Nature Electronics、Nature Energy、Nature Materials
- **ScienceDirect RSS（3 個）** — Solid-State Electronics、MSEB、Applied Surface Science

### 企業白名單 / Company Whitelist

預設 8 家企業，自動生成 Google News RSS 來源：

Default 8 companies with auto-generated Google News RSS sources:

| 企業 / Company | 域名 / Domain |
|----------------|---------------|
| VIS 世界先進 | vis.com.tw |
| Infineon | infineon.com |
| onsemi | onsemi.com |
| ST Microelectronics | st.com |
| Navitas Semiconductor | navitassemi.com |
| Wolfspeed | wolfspeed.com |
| Texas Instruments | ti.com |
| Renesas | renesas.com |

新增企業後，系統自動建立查詢格式：
After adding a company, the system auto-creates a query in this format:

```
site:{domain} ("gallium nitride" OR ("GaN" semiconductor)) -generative -adversarial when:14d
```

### 來源驗證 / Source Verification

每個來源需通過三段驗證：

Each source goes through three-stage verification:

1. URL 格式驗證 / URL format check
2. 可信域名比對（49 個靜態白名單）/ Trusted domain match (49 static entries)
3. HTTP 可達性測試 / HTTP reachability test (15s timeout)

| 狀態 / Status | 說明 / Description |
|---------------|---------------------|
| `verified` | 可達 + 命中可信域名 / Reachable + trusted domain |
| `unverified` | 可達但未命中白名單（可人工確認）/ Reachable but not in whitelist (manual approval possible) |
| `failed` | 不可達或格式異常 / Unreachable or invalid format |

---

## 報告系統 / Reporting System

系統自動生成三種等級的中文情報報告並 Email 發送。

The system auto-generates three tiers of Chinese intelligence reports delivered by email.

| 類型 / Type | 觸發時間 / Trigger | DeepSeek 摘要字數 / Summary Length | 文章數 / Articles |
|-------------|--------------------|------------------------------------|-------------------|
| 日報 / Daily | 每日 08:00 | 100–150 字 | 最近 24 小時前 20 篇 |
| 週報 / Weekly | 週一、週四 07:00（可設定）| 200–280 字 | 最近 7 天前 30 篇 |
| 月報 / Monthly | 每月底 08:10 | 350–450 字 | 本月前 40 篇 |

**月報結構 / Monthly Report Structure:**
1. 本月整體動態總結 / Overall monthly summary
2. 與上月比較（數量、分類分佈變化）/ Comparison vs. prior month
3. 重點技術 / 產業事件點評 / Key tech/industry event commentary
4. 下月值得關注的方向 / Forward-looking signals for next month

---

## Web 面板操作 / Web Panel

開啟 `http://127.0.0.1:8001/` 後可使用以下功能：

After opening `http://127.0.0.1:8001/`, the following features are available:

### 情報瀏覽 / Intelligence Feed
- 按一級分類 / 技術分類 / 關鍵詞 / 回看天數 / 日期區間篩選文章
- 在每篇文章卡片中手動調整分類並儲存
- 查看 DeepSeek 分析摘要、情緒分數、影響力分數

### 企業白名單 / Company Whitelist
- 新增 / 啟停企業白名單
- 一鍵同步：自動為所有企業建立或更新 Google News RSS 來源

### 資訊來源管理 / Source Management
- 按一級分類查看所有來源
- 新增 / 啟停自訂來源
- 觸發來源可達性複核
- 人工確認未驗證來源（`unverified → approved`）

### 股票監控 / Stock Monitor
- 最新快照表格（各 ticker 最新價格、漲跌幅）
- 折線圖（按 ticker 和時間點數顯示）
- 一鍵回灌歷史資料（預設 `5d × 15m`）

### 文章補全 / Article Enrichment
- 對缺少摘要或正文的文章觸發頁面內容補全（BeautifulSoup 抓取）

### 手動觸發任務 / Manual Task Triggers
- 立即執行抓取 / Trigger immediate ingestion
- 發送週報 / Send weekly report
- 股票快照更新 / Refresh stock snapshots

---

## 排程任務 / Scheduled Jobs

所有排程以 `TIMEZONE`（預設 Asia/Taipei）執行：

All jobs run in the configured `TIMEZONE` (default: Asia/Taipei):

| 任務 / Job | 頻率 / Frequency | 說明 / Description |
|-----------|-----------------|---------------------|
| `ingest_job` | 每 N 分鐘（預設 120）/ Every N min | 多源抓取 + 分類 / Multi-source scrape + classify |
| `stock_job` | 每 30 分鐘 / Every 30 min | 股票快照更新 / Stock snapshot refresh |
| `daily_report_job` | Cron 每日 08:00 | 日報 Email / Daily report email |
| `weekly_report_job` | Cron 週一、週四 07:00 | 週報 Email / Weekly report email |
| `monthly_report_job` | Cron 每月底 08:10 | 月報 Email / Monthly report email |

> 若某次抓取無新增文章，通常代表該批 URL 已存在或被相關性過濾；可在 Web 面板查看 `fetched / inserted / skipped` 統計確認排程正常運行。
> If a scrape adds no new articles, those URLs likely already exist or failed the relevance filter. Check the `fetched/inserted/skipped` stats in the Web panel to confirm the scheduler is running.

---

## 主要 API / Key API Endpoints

| 方法 / Method | 路徑 / Path | 說明 / Description |
|---------------|------------|---------------------|
| GET | `/api/articles` | 文章列表（支援篩選）/ Article list (with filters) |
| PUT | `/api/articles/{id}/classification` | 手動調整分類 / Manual reclassification |
| GET | `/api/stats` | 抓取統計 / 分類分佈 / Ingest stats + category distribution |
| GET | `/api/stocks` | 最新股票快照 / Latest stock snapshots |
| GET | `/api/stocks/{ticker}/series` | 股票歷史折線 / Stock history series |
| GET | `/api/source-sites` | 所有資料來源 / All sources |
| POST | `/api/source-sites` | 新增來源 / Add source |
| PUT | `/api/source-sites/{id}` | 更新來源 / Update source |
| POST | `/api/source-sites/{id}/verify` | 複核可達性 / Re-verify reachability |
| POST | `/api/source-sites/{id}/approve` | 人工核准 / Manual approval |
| GET | `/api/categories` | 分類樹 / Category tree |
| POST | `/api/categories` | 新增分類 / Add category |
| PUT | `/api/categories/{id}` | 更新分類 / Update category |
| DELETE | `/api/categories/{id}` | 刪除分類 / Delete category |
| GET | `/api/company-whitelist` | 企業白名單 / Company whitelist |
| POST | `/api/company-whitelist` | 新增企業 / Add company |
| POST | `/api/company-whitelist/sync` | 同步白名單來源 / Sync whitelist sources |
| POST | `/api/tasks/ingest` | 立即抓取 / Trigger immediate ingestion |
| POST | `/api/tasks/stocks` | 更新股票快照 / Refresh stock snapshots |
| POST | `/api/tasks/stocks/backfill` | 歷史資料回灌 / Historical backfill |
| POST | `/api/tasks/enrich-content` | 補全文章正文 / Enrich article content |
| POST | `/api/tasks/send-daily` | 手動發送日報 / Send daily report manually |
| POST | `/api/tasks/send-weekly` | 手動發送週報 / Send weekly report manually |
| POST | `/api/tasks/send-monthly` | 手動發送月報 / Send monthly report manually |

---

## 專案結構 / Project Structure

```
app/
├── api.py              # FastAPI 路由 / Routes & startup hooks
├── pipeline.py         # 抓取 + 相關性過濾 + 分類 / Ingestion + filtering + classification
├── taxonomy.py         # 確定性規則分類引擎 / Deterministic rule engine
├── deepseek_client.py  # DeepSeek LLM 整合 / LLM integration
├── sources.py          # 25 個預設來源定義 + 抓取邏輯 / 25 default sources + fetch logic
├── source_registry.py  # 來源管理 + 驗證 + 白名單同步 / Source management + verification
├── category_registry.py # 分類樹 CRUD / Category tree CRUD
├── reporter.py         # 三級報告生成 + Email 發送 / Report generation + email delivery
├── scheduler.py        # APScheduler 任務管理 / APScheduler job management
├── models.py           # SQLAlchemy ORM 模型 / ORM models
├── db.py               # 資料庫初始化 / Database initialization
├── config.py           # 設定管理 / Settings management
└── templates/
    └── index.html      # Web 面板 / Web panel
main.py                 # 入口點 / Entry point
```

---

## 雲端部署 / Deployment

### Railway 部署 / Railway Deployment

```bash
uvicorn app.api:app --host 0.0.0.0 --port $PORT
```

**Railway 環境變數 / Environment Variables:**

```
DEEPSEEK_API_KEY       = your_deepseek_api_key
GMAIL_USER             = your_backup@gmail.com
GMAIL_APP_PASSWORD     = xxxx xxxx xxxx xxxx
GMAIL_TO               = your_work@gmail.com
STOCK_TICKERS_CSV      = NVTS,ON,STM,IFNNY,WOLF,TXN
INGEST_INTERVAL_MINUTES = 120
TIMEZONE               = Asia/Taipei
```

### 資料庫升級 / Database Upgrade

預設使用 SQLite，生產環境建議升級至 PostgreSQL：

Default is SQLite. For production, upgrade to PostgreSQL:

```dotenv
DB_URL=postgresql://user:password@host:5432/ganiq
```

---

## 授權聲明 / License Notice

本系統核心代碼受自訂授權條款（Source Available License v2.0）保護。

This software is protected under a custom Source Available License v2.0.

- 任何使用、下載、部署、衍生開發，均須明確標示作者：**Ping yu-Chen, Taiwan**
- Any use, download, deployment, or derivative work must clearly credit: **Ping yu-Chen, Taiwan**
- 商業使用須事先取得書面授權 / Commercial use requires prior written authorization
- 商業授權洽詢 / Commercial licensing: **chenbill718@gmail.com**

詳見 [LICENSE](./LICENSE) / See [LICENSE](./LICENSE) for full terms.

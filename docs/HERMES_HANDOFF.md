# HERMES 交接文件 — websitecheck 雲端化

> 讀者：**Hermes Agent**（接手執行雲端化的 agent）。
> 本文件是可直接照做的規格。先讀 `docs/ARCHITECTURE.md`（系統全貌）與 `docs/SECURITY_CLOUD.md`（機敏處理）。
> 撰寫時系統狀態：branch `main`，最新 commit 為 docs 系列；`private/` 全 gitignore。

---

## 0. 一句話目標
把目前「本機 Windows + 地端 AI + 手動跑」的 466 站政府網站稽核系統，搬成
**雲端排程：每天自動掃一小批、只讀 Google Sheet 拿設定、機敏走 Secret Manager、產出寫私有物件儲存**。

## 1. 系統現況（你要接手的東西）
- **三個子系統**（見 ARCHITECTURE.md §2）：`monthly/`（每月合規檢核）、`daily/`（每日連結稽核+寄信）、`engine/`（統一引擎，含 `full_overnight.py` 四階段深掃）。
- **主力深掃** = `python -m engine.full_overnight`：階段1 站層級多行程爬取 → 階段2/3 對可疑外連做地端 AI 複查 → 階段4 產 `CONFIRMED_hijacks.csv`/`summary.json`。支援 `--org`/`--only`/`--resume`。
- **資料單一來源** = Google 試算表（`TaipeiCityGovWebsiteCheck`）：
  - 分頁 `主設定表`（14 站，每月檢核設定）
  - 分頁 `TCGweb466站清單`（466 站主資料，含 `內容抓取方式`/`depth`/`pagination`/`頁數`/`web_check14站`…）
- **設定** 目前在 `private/config.json`（機敏）；`config.py` 為載入器，讀序 `private/config.json → 根目錄 → example`，且**每個 key 可被同名大寫環境變數覆寫**（關鍵：雲端就靠這個注入）。
- **時間模型**（實測）：每頁爬取 ~0.27s（6 workers）、每筆 AI 複查 ~3s；**AI 只佔總時間 1–3%，瓶頸是頁數（尤其單一巨站）**。

## 2. 前置產出在哪（本次已備好）
| 產出 | 位置 | 用途 |
|---|---|---|
| 架構全貌 | `docs/ARCHITECTURE.md` | 先讀 |
| 掃描設定分頁設計 | `docs/cloud/scan-settings-tab.md` | 上雲設定來源（Sheet 新分頁）內容 |
| 30 天排程 | `private/cloud/monthly-schedule.md`（**未進版控**：含逐站清單，屬營運敏感資料，不放公開 repo） | 每天掃哪些站、頁數、估時 |
| 失效網站清單 | `private/cloud/dead-sites.md`（**未進版控**：同上，含失效政府網域） | 13 站疑似廢站（只列不刪） |
| 停更站清單 | `private/cloud/stale-5months.md`、`private/cloud/stale-nodate-recheck.md`（**未進版控**） | 超過5個月未更新/無日期站 |
| 資安/機敏指引 | `docs/SECURITY_CLOUD.md` | 哪些不可公開、怎麼注入 |
| Sheet 待寫入 grid（json） | scratchpad（`settings_grid.json`/`schedule_grid.json`/`dead_grid.json`） | 承辦核准後寫入 Sheet 的實際內容 |

> ⚠ 三個 Sheet 新分頁（`掃描設定`/`月度掃描排程`/`失效網站清單`）**尚未寫入 Sheet**，等使用者核准。Hermes 執行時若已核准，用 grid json 以「新增分頁、非破壞」方式寫入。

## 3. 上雲目標架構（建議）
```
Cloud Scheduler (cron, 每天一次)
   └─> Cloud Run Job / Container(執行 engine.full_overnight 的當日批)
         ├─ 讀 Google Sheet「掃描設定」分頁 → 全域參數
         ├─ 讀「月度掃描排程」分頁 → 今天(Day N)該掃哪些站
         ├─ 讀「TCGweb466站清單」→ 每站 url/內容抓取方式/頁數
         ├─ 機敏由 Secret Manager 注入為 env(見 SECURITY_CLOUD §D)
         ├─ AI 複查打 AI_BASE_URL(地端保留 or 換雲端)
         ├─ 產出寫私有 GCS bucket(reports/…)
         └─ 掃完把真實頁數 write_sheet 回填「頁數」欄
```

## 4. 待 Hermes 執行的步驟（可照做）

### Step 1 — 設定改吃環境變數（程式微調）
- 現況 `config.py` 已支援 env 覆寫；但 `engine/*` 有**硬編絕對路徑** `sys.path.insert(0, r"D:\websitecheck")` 與硬編 CSV 路徑（見 ARCHITECTURE §6「硬編絕對路徑」）。
- 動作：把 `CSV_LIST`/`CSV_DEFAULT` 與 `sys.path` 改讀 `config.BASE_DIR`/`config.PRIVATE_DIR` 或 env `WEBCHECK_HOME`。**這是可攜性的必要前置**，不改雲端會斷。

### Step 2 — Sheet 作為設定來源（讀取）
- 新增讀取層：開 `掃描設定` 分頁 → 解析 A 區 key/value 成 dict → 覆蓋預設。
- `full_overnight` 目前吃 CLI 參數（`--max-pages`/`--workers`）與 `config._cfg["scan"]`；改成優先讀 Sheet 的 `掃描設定`，缺項才退回 config/env。
- 每站設定沿用既有：`page_budget.read_sheet()` 已讀「頁數」欄；`內容抓取方式`/`depth`/`pagination` 從 `TCGweb466站清單` 讀。

### Step 3 — 機敏注入（Secret Manager → env）
- 依 `SECURITY_CLOUD.md §D`：設 `AI_BASE_URL/AI_MODEL/AI_API_KEY/MASTER_SHEET_ID/GA_KEY_FILE/GMAIL_APP_PASSWORD`。
- `GA_KEY_FILE` 指向掛載的 service account 檔（或改用 workload identity，免金鑰檔）。
- **不要**把 `config.json` 打進映像檔。

### Step 4 — 雲端排程（每天一批）
- Cloud Scheduler 每天觸發 Cloud Run Job。
- Job 啟動時算「今天是這個月第幾天 Day N」→ 讀 `月度掃描排程` 分頁 Day N 的站名清單 → 轉成 `--only "<url1>,<url2>,…"` 或直接把清單餵進 full_overnight。
- 建議用 `--resume`/當日獨立 outdir，讓中斷可續。

### Step 5 — 巨站拆時段落地（6 個 ≥9000 站）
- 排程已把 6 巨站放各自獨立日（Day 5/10/15/20/25/30）。
- 每個巨站日單站 ~9000 頁、估 ~55 分鐘（實測外推）。若雲端單次執行時間受限：
  - 方案 A：巨站日仍一次跑完（時間夠就最省事）。
  - 方案 B：拆時段 — 需給爬蟲加 **path-scope** 能力（目前 `crawl_internal` 只認 host），例如孔廟按 `/zh-tw/C/`、`/zh-tw/L/` 分段，分兩個時段各爬一半。此為**新功能**，需實作＋測試。
- **孔廟併爬**：中/英/日/韓 4 版同 host `www.tctcc.taipei`，一次爬即涵蓋全語言 → daily 深掃只跑中文那筆即可，另 3 筆標「隨中文帶掃」，省 ~27,000 次重複抓取（見 `private/cloud/monthly-schedule.md` 註記）。

### Step 6 — 產出與回填
- reports/ 寫私有 bucket；`summary.json`/`CONFIRMED_hijacks.csv` 為關鍵輸出。
- 掃完 `page_budget.write_sheet()` 回填「頁數」欄（URL 對鍵、只增不減）——雲端要有 Sheet 寫入權。
- 寄信（daily）：雲端用 `method=gmail`（SMTP + app_password），Outlook 模式僅限本機。

### Step 7 — 公開前資安複掃（若 repo 要 public）
```
# 內部 AI 主機名全稱不寫在本文件；從 private/config.json 取值後再掃，避免把它寫進公開檔
AIHOST=$(python -c "import json;print(json.load(open('private/config.json'))['ai_base_url'].split('//')[1].split('/')[0])")
git ls-files -z | xargs -0 grep -InE "$AIHOST|@gov\.taipei|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9]{20}|-----BEGIN"
git ls-files private/        # 必須為空
```

## 5. 已知地雷（別踩）
- **寫 Sheet 一律 URL 對鍵、逐列對準，不可按列序**（既有鐵律）；任何寫入用「新增分頁/新增欄」，不覆蓋既有。
- `wifi.taipei` 不可升級渲染（會撞 500）；`travel.taipei` Cloudflare 擋 headless → 這兩個在 `fetch_layered` 已硬編處理，別改。
- AI 複查對**死域名候選**會重試 3×(20s timeout+4s) → 單筆最壞 ~70s；候選多為死站時 stage2/3 會變長（見 ARCHITECTURE §時間模型）。可考慮：已知 DEAD 的候選跳過 AI。
- `疑似失效`/`manual` 站不掃（`SKIP_METHODS`）；`3d.taipei` 硬跳過。
- 跨行程子程序的 crawl print 在 log 會 Big5 亂碼（不影響結果）。

## 6. 驗收標準（Hermes 做完該達到）
1. 雲端 Job 能**只靠 Sheet + env** 跑完某一天的批，無需本機 config.json。
2. 6 巨站各自獨立日跑完或拆時段跑完，不撞雲端執行時限。
3. 產出寫私有 bucket，頁數回填 Sheet 成功。
4. repo 若 public，資安複掃（Step 7）零命中。
5. 一個完整月（30 天）跑完可覆蓋全 443 可掃站。

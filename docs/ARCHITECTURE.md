# websitecheck 專案架構文件

> 本文件由程式碼全面走讀後整理，供日後回來快速上手。**只描述現況、不改功能**。
> 對應 commit：`2e6c9bb`（連結稽核誤報根因修正 + engine/ 引擎進版控）。

---

## 1. 這個專案在解決什麼問題（白話版）

臺北市政府（資訊局）要定期確認轄下數百個網站「還健康、沒出事」。實務上有兩種痛點：

1. **每月合規檢核**——每個機關網站要符合一堆規定：HTTPS 憑證有沒有過期、有沒有轉址、
   手機版（RWD）、站內搜尋、無障礙標章、失效連結、最新消息有沒有在更新……
   以前要人工一站一站點開檢查，很花時間。

2. **網域被搶註 / 掛馬**（這是這個專案最核心、也最花心力的部分）——
   政府網站常在新聞稿、活動頁裡連到「當年委外廠商的活動官網」。活動結束、網域到期沒續約後，
   **博弈/色情集團會把這個過期網域註冊走**，於是政府網站等於在幫賭場/色情站導流。
   數發部 115/6/8 發文要求各機關清查「委外案或活動結束後未移除的網址」，這個專案就是把清查自動化：
   **爬完整站 → 收集所有對外連結 → 逐一檢查連結目標是不是失效、被搶註、變成賭博色情或停放頁**，
   有問題就寄信通知該機關。

**難點在於「不能亂喊狼來了」**：反毒宣導頁會提到「賭博」、藝文報導會出現「色情」字眼、
法文網頁的 `spécialiste` 會誤撞 `cialis`——單靠關鍵字比對會製造大量誤報，讓報告失去公信力。
因此本專案的一大重心是**兩關把誤報壓下去**（機械圈選 → 地端 AI 讀全文複查），詳見 §5。

---

## 2. 兩大子系統 + 一個統一引擎

| 子系統 | 資料夾 | 頻率 | 做什麼 | 產出 |
|---|---|---|---|---|
| **每月檢核** | `monthly/` | 每月（本機 Windows） | HTTPS/RWD/連結/站內深爬、GA 流量、AI 內容判讀 | 檢核表 Excel + 月報 md/json |
| **每日連結稽核** | `daily/` | 每日（主機 cron） | 全站爬取、查連結失效/搶註/賭博色情，逐站寄信 | 逐站 CSV + Email |
| **統一引擎** | `engine/` | 手動 / 整夜跑 | 把「靜態優先抓取」做成共用地基，上層長出健康剖面、合規剖面、**全 466 站深度稽核** | reports/ 下各式 json/csv |

三者**共用同一份設定 `config.py`、同一個 Google 服務帳戶、同一張主試算表**。
`engine/` 是後來的整併層——它把 `daily`（連結稽核）與 `monthly`（AI 判讀、日期抽取）的能力
接起來，並疊出「全站深度稽核」這個 `daily`/`monthly` 都沒有的新剖面。

### 資料的單一事實來源：Google 試算表「主設定表」
- 唯一手動維護的地方。欄位含：網站名稱、網址、局處、`內容抓取方式`（code/ai/playwright/manual/疑似失效）、
  `web_check14站`（是否納入合規 AI 判讀）、`頁數`（每站上次爬到幾頁，見 §5 加碼爬頁）。
- `monthly/sync_config.py` 從主設定表產出兩個下游檔：`private/sites.json`（monthly 用）、
  `private/domains.txt`（daily 寄信用）。
- 離線 / 大量掃描時改讀本機快照 `private/TCGweb_466站對照清單_v2.csv`（466 站）。

---

## 3. 資料夾與檔案結構（逐檔職責）

```
websitecheck/
├─ config.py                 共用設定載入器(private/config.json → 根目錄 → example)
├─ config.example.json       設定範本(公開)
├─ read_sheet.py             【一次性診斷小工具】連主設定表、印前 20 站，確認 Sheet 連線 OK
├─ 每月檢核.bat / 每日稽核.bat  兩個一鍵入口(純 ASCII 檔名以外的中文，注意編碼)
├─ requirements.txt
│
├─ monthly/   ── 每月合規檢核 ────────────────────────────────
│   ├─ monthly_check.py   主流程：對每站查 HTTPS/憑證/HSTS、RWD、檢索、無障礙、
│   │                     首頁連結失效、站內深爬(呼叫 deep_check)、AI 內容判讀，
│   │                     並把 daily 的連結稽核結果(read_link_audit)併進月報
│   ├─ deep_check.py      站內深度 BFS(≤150頁)：內部失效連結 + Office 檔缺 PDF/ODF 替代版
│   ├─ node_check.py      檢核表(一) 逐節點 AI 內容判讀
│   ├─ ga_traffic.py      撈 GA4 screenPageViews(檢核表的「網站流量數」)
│   ├─ probe_method.py    探測每站「該用哪種抓取方式」→ 寫回主設定表「內容抓取方式」欄
│   ├─ sync_config.py     主設定表 → sites.json + domains.txt
│   ├─ update_excel.py    把結果寫進網站檢核表 Excel
│   ├─ webcheck_ai.py     ★ 共用 AI 模組：fetch_html / html_to_text / ask_ai
│   │                     (支援 openai 相容 / anthropic / gemini 三家，engine 也重用它)
│   ├─ smoke_test.py      部署冒煙測試(不寫檔、不改線上)
│   └─ 每月建立檢核表.gs   Google Apps Script(在試算表端)
│
├─ daily/    ── 每日對外連結稽核 ────────────────────────────
│   ├─ audit_links.py     ★★ 連結稽核引擎核心。爬單站 → 收外連 → 逐連結判風險。
│   │                     所有搶註/賭博/色情/失效判斷、誤報防呆都在這支(見 §5)
│   ├─ batch_audit.py     批次殼：讀 domains.txt，逐站 audit_site → 逐站 Outlook/Gmail 寄信
│   │                     (--daily 分 5 組按星期輪掃；--draft/--no-mail/--only/--sample 等旗標)
│   ├─ run_daily.sh/.bat  排程入口
│   └─ 排程部署說明.txt
│
├─ engine/   ── 統一引擎(整併地基) ───────────────────────────
│   (詳見 §3.1，共 10 檔)
│
└─ private/  ── 機敏/個資/產出，整個 gitignore ─────────────
    ├─ config.json / ga-service-account.json
    ├─ sites.json / domains.txt / nodes_map.json
    ├─ TCGweb_466站對照清單_v2.csv   466 站離線快照(engine 大量掃描讀這支)
    └─ reports/…                     各式報告(full_overnight_* / linkaudit_all_* / engine_run_*)
```

### 3.1 engine/ 那 10 個檔

分成三層：**地基（抓取）→ 中層（爬取/清單）→ 上層（驅動/剖面/報告）**。

| 檔 | 層 | 職責 |
|---|---|---|
| `__init__.py` | — | 只放整併說明 docstring，無程式碼 |
| **`fetch_layered.py`** | 地基 | ★ **靜態優先分層抓取**。先靜態抓 → `detect_shell` 判是不是 JS 空殼/frameset/內容過少 → 只有必要且 `allow_render=True` 才升級 Playwright 渲染。硬編兩個陷阱名單：`NEVER_RENDER`(wifi.taipei 渲染會撞 500)、`FORCE_MANUAL`(travel.taipei 有 Cloudflare 人機驗證) |
| `dates.py` | 地基 | 從 HTML 抽「最新更新日期」並正規化(移植自 TCGweb 的日期抽取器) |
| **`crawl.py`** | 中層 | 深層 BFS 爬蟲(`crawl_site`)。每頁走 `fetch_layered`，做 sitemap 優先、內/外連結拆分、同標題+分頁去重、外連狀態檢查、日期抽取。**產健康向的頁面清單**，非搶註判斷 |
| `page_budget.py` | 中層 | 每站頁數自適應：讀/寫主設定表「頁數」欄。`get_cap`=上次頁數+100；掃完把本次真實頁數寫回(URL 對鍵、只在變大時更新) |
| **`scan.py`** | 上層 | 雙剖面驅動：`health_profile`(最新消息日期/停更判定/抓取方式) + `compliance_profile`(14 站送地端 AI 判「首頁有無最新消息區塊」)。`load_sites` 支援 Sheet 或 CSV |
| **`run_all.py`** | 上層 | 日常執行殼：一次跑 健康剖面(全站) → 合規剖面(14站 AI) → 選擇性深爬(`--deep`/`--deep-stale`)，產綜合摘要 |
| **`full_overnight.py`** | 上層 | ★★ **全 466 站四階段深度稽核**(整夜無人值守)。見 §4。這是目前 daily 稽核的「重裝版」主力 |
| `link_audit_all.py` | 上層 | 全 466 站連結稽核的**早期簡化版**：站層級多行程平行，固定頁數、無加碼、無續跑。功能已被 `full_overnight.py` 階段1 涵蓋(見 §6 技術債) |
| `verify_suspicious.py` | 上層 | SUSPICIOUS 第二關**獨立版**：讀既有 all_problems.csv → 對搶註候選送地端 AI 判 A/B/C。此邏輯已被 `full_overnight.py` 階段2 內建(見 §6) |

---

## 4. 端到端流程：資料怎麼流動

### 4.1 每日連結稽核（主線，`full_overnight.py` 四階段）

這是專案最核心的資料流。輸入 466 站 CSV，輸出「確認的搶註/掛馬清單」。

```
主設定表/CSV (466 站，含 內容抓取方式、頁數)
   │  csv.DictReader，套 --org/--only/--limit 過濾
   │  method∈{manual,疑似失效} 或 3d.taipei → 跳過(只記錄不掃)
   ▼
【階段1】站層級多行程平行(ProcessPoolExecutor, 每站獨立行程 max_tasks_per_child=1)
   每站 audit_one：
     ALLOW_RENDER = (method=="playwright")         ← 渲染只給 playwright 站
     首掃上限 = page_budget.get_cap(頁數欄+100)
     audit_links.audit_site(url, cap):
        crawl_internal  BFS 爬站內頁(≤8 執行緒)，收集所有【對外】連結 + 出現位置
                        分頁參數(page/date/…)不再往下挖，避免月曆無限頁
        check_external  每條外連逐一判風險(見 §5 判斷樹)
     撞上限 → 加碼 1000→3000→6000→9000 封頂，直到爬完或到頂
     爬到 0 頁 → 開渲染重試 ≤2 次(SPA 空殼保護)
   邊完成邊 flush：all_problems.csv(所有異常) + progress.json(每站進度)
   收尾：把本次各站真實頁數 write_sheet 寫回主設定表「頁數」欄
   ▼
【階段2+3】對階段1 判為 SUSPICIOUS 的外連(去重後)：
   階段2 ai_verify：實連抓內容(重試3次) → 地端 AI 判 A賭博色情 / B停放出售 / C誤報正當
                    (「根路徑命中」的候選，改驗根路徑，因深層頁常是乾淨的)
   階段3 characterize：AI 判 A/B 者，抓原始 HTML 判入侵型態
                    (隱藏掛馬 hidden / 可見注入 / 網域易主 takeover / 停放失效)
   邊做邊寫 suspicious_verified.json
   ▼
【階段4】產最終報告：
   suspicious_verified.csv   全部複查結果(含 A/B/C 判定 + 證據)
   CONFIRMED_hijacks.csv     只留 AI 判 A(真搶註/掛馬) → 這份才是要辦的
   summary.json              統計(掃幾站/各風險數/AI 判定分布/確認數/耗時)
```

**`--resume <dir>` 續跑**：載入該目錄 progress.json 中「非 fail」的 URL 當作 done_urls，
掃描時跳過，`all_problems.csv` 以 append 開啟。可把整夜大掃分次補完(見本次教育局續跑)。

### 4.2 每日寄信版（`batch_audit.py`，較輕量）

`domains.txt` → 逐站 `audit_site`(同一個 audit_links 引擎) → 有異常才寄信(Outlook 或 Gmail SMTP)。
`--daily` 會把清單分 5 組、按今天星期幾只掃一組(分散負載)。這是**實際排程在跑的每日寄信**；
`full_overnight.py` 則是要完整盤點/找搶註時手動跑的重裝版。

### 4.3 每月合規檢核（`monthly_check.py`）

`sites.json` → 每站查 HTTPS 憑證/HSTS、RWD、檢索、無障礙標章、首頁連結失效、
`deep_check` 站內深爬、AI 內容判讀 → **並讀入 daily 的 `links_*.jsonl`/`problems_*.csv`
把連結稽核結果併進月報** → 產 `report_<民國月>.md` + `result_<月>.json`。
兩套子系統在這裡交會：monthly 的報告會引用 daily 的稽核成果。

### 4.4 engine 雙剖面（`run_all.py` / `scan.py`）

健康剖面(全站首頁層、靜態優先、快) + 合規剖面(14 站送 AI)，可選深爬停更站。
偏「站況體檢」，與 §4.1 的「搶註獵捕」互補。

---

## 5. 關鍵設計決策

### (A) 誤報根因修正——把「亂喊狼來了」壓下去（本次 commit 重點）
連結稽核最大的風險是誤報。`audit_links.check_external` 的判斷樹分層擋掉各類假警報：

1. **政府專屬網域只驗存活、不掃內容**：`GOV_EXCLUSIVE_SUFFIXES`(`.gov.tw`/`.mil.tw`/`.gov.taipei`)
   依 TWNIC 規章民間根本註冊不到 → 不可能被搶註。DNS 失敗也只標「服務下線/內網限定」，
   不再誤標「疑遭搶註」。**注意 `.edu.tw`/`.org.tw`/`.taipei` 民間可註冊，不列入、照樣全檢**。
2. **社群/短網址只驗存活**：`SOCIAL_SKIP_HOSTS`(line/fb/youtube/bit.ly…)重導到他域是設計行為，不判搶註。
3. **關鍵字整字比對 + 善意詞剔除**：英文詞用邊界比對(避免 `specialise` 撞 `cialis`，
   邊界字元含拉丁擴充避免法文 é 破功)；中文子字串比對；先剔除 `白色情人節`、
   `.casino`/`.poker`(註冊商當商品名賣的 TLD)等善意詞。
4. **根路徑複查**：搶註者常只在首頁放賭場，深層頁 `/default.html` 回 200 且乾淨。
   被連的深層頁沒中關鍵字時，`_root_keyword_hits` 會另抓根路徑掃一次(每 host 快取一次)
   （真實案例：`taitraesource.com/default.html` 乾淨、`/` 是 Dewa77 賭場）。
5. **第二關 AI 複查**：機械判 SUSPICIOUS 只是「候選」，一定再送地端 AI 讀全文判 A/B/C；
   抓取失敗重試 3 次才降級，避免一次網路抖動把真掛馬誤降成停放。防治/反毒/藝評一律算 C。

### (B) 靜態優先、例外才渲染
TCGweb 舊架構「每頁必渲」很吃資源。`fetch_layered` 改成靜態優先，`detect_shell` 判到
JS 空殼/frameset/內容過少才升級 Playwright，實測 466 站僅約 10% 需渲染。
`audit_links` 端再加 `_RENDER_CAP=60` 每站渲染預算，避免整站 SPA 每頁都渲染。
**渲染由「內容抓取方式」欄驅動**：只有標 `playwright` 的站才會 `ALLOW_RENDER=True`。

### (C) 加碼爬頁 + 頁數自適應
- 大站不知道有幾頁：首掃撞上限就**加碼** 1000→3000→6000→9000 封頂，直到爬完。
- 每站上次真實頁數記在主設定表「頁數」欄；下次首掃上限 = 該值 + 100(`BUFFER`)。
  掃完只在「這次爬更多」時把值寫大 → 記錄各站最大規模，且分頁跳過讓陷阱頁不灌大這個值。

### (D) 站層級多行程平行 + 逐項 flush
每站一個獨立子行程(`max_tasks_per_child=1`)：`ALLOW_RENDER`/渲染額度各自獨立不互相干擾，
且每站跑完就釋放記憶體。所有產出邊完成邊 flush 寫檔，中途可查 progress、可 `--resume` 續跑。

### (E) 0 頁站保護
掃出 0 頁有兩種：暫時性抓空(重跑會恢復) vs SPA 空殼(需渲染)。`audit_one` 對 0 頁站
開渲染重試 ≤2 次——靜態有料者重試仍走靜態即成功，SPA 空殼者這次被渲染救回。

### (F) AI 供應商可換（`webcheck_ai`）
同一套 `ask_ai` 依 `config.ai_provider` 切 openai 相容(地端 vLLM/Ollama 或雲端) / anthropic / gemini。
地端跑(如記憶中的 <internal-ai-host>)可讓個資不出機關；雲端則裝 SDK 即用。

---

## 6. 技術債 / 重複 / 可清理處

> 以下為走讀觀察，**非本次要動的項目**，供日後重構參考。按影響程度排序。

### 高：三處重複的「同一件事的不同版本」
1. **全站連結稽核有兩份**：`engine/link_audit_all.py` 是 `full_overnight.py` 階段1 的早期簡化版
   （無加碼爬頁、無 page_budget、無 --resume、無 0 頁重試）。`audit_one` 兩邊各寫一份且會漂移。
   → `full_overnight` 已完全涵蓋，`link_audit_all` 可考慮標為 deprecated 或移除。
2. **SUSPICIOUS 第二關有兩份**：`engine/verify_suspicious.py` 與 `full_overnight.py` 的
   `ai_verify`/階段2 是同一套 A/B/C 複查邏輯，AI 問題字串幾乎一樣但各寫一份。
3. **賭博/色情關鍵字清單有兩份**：`audit_links.SUSPICIOUS_KEYWORDS` 與 `full_overnight.BAD_KW`
   內容重疊卻獨立維護，改了一邊另一邊不會同步 → 建議抽成單一來源 import。

### 中：三個 BFS 爬蟲、多條 HTML→text 路徑
- 專案裡有**三個爬蟲**各為其目的：`audit_links.crawl_internal`(外連導向, requests+BS4)、
  `engine/crawl.crawl_site`(健康/日期導向, urllib+fetch_layered)、`monthly/deep_check`(站內失效導向)。
  三者都做 BFS + 抽連結 + 分頁跳過，但去重與外連處理各異。短期難合併(目的不同)，但
  `PAGINATION_PARAMS` 在 `audit_links` 與 `engine/crawl` 各定義一份且集合不同，易漂移，可先統一。
- HTML 轉文字散在 `webcheck_ai.html_to_text`、`audit_links` 的 BS4 `get_text`、
  `full_overnight.visible_text` 三處。

### 中：硬編絕對路徑，不可攜
- `engine/*` 幾乎每支開頭 `sys.path.insert(0, r"D:\websitecheck")`，且 `CSV_LIST` /
  `CSV_DEFAULT` 把 `D:\websitecheck\private\TCGweb_466站對照清單_v2.csv` 寫死在 3 個檔。
  → 專案已有 `config.BASE_DIR`/`config.PRIVATE_DIR`，engine 卻沒用；換機器或改路徑會全斷。
  建議統一改讀 config。（`verify_suspicious` 的 glob 路徑同樣寫死。）

### 中：`SKIP_METHODS` / `SKIP_HOSTS` 常數在 `full_overnight` 與 `link_audit_all` 各一份。

### 低：報告目錄無保留策略
- `private/reports/` 已累積約 140+ 個 `full_overnight_*` 目錄，多是 1~2 站的定點重測。
  `batch_audit` 有 `purge_old_logs`，但 reports 沒有對應清理。建議加保留天數/自動歸檔。

### 低：`read_sheet.py`(根目錄) 定位模糊
- 只是連 Sheet 印前 20 站的**一次性診斷腳本**，與 `page_budget.read_sheet`、`scan.load_sites`
  三處各有一套讀表邏輯。可移到 `scripts/`(或 monthly/)並註明「診斷用」，避免被誤認為正式入口。

### 低：跨行程 print 編碼
- 子行程內 `audit_links` 的 crawl 進度 `print` 在被重導到 log 時會呈現 Big5 亂碼
  （`full_overnight` 自己的 `[階段x]` 行有 `reconfigure(utf-8)` 是乾淨的）。不影響結果，追 log 時知道即可。

---

## 7. 常用指令速查

```bash
# 全站四階段深度稽核(整夜)
python -m engine.full_overnight --max-pages 8000 --workers 6
# 只掃某局處 / 定點重測 / 續跑
python -m engine.full_overnight --org 教育局 --workers 6
python -m engine.full_overnight --only taitraesource --workers 3
python -m engine.full_overnight --org 教育局 --resume full_overnight_20260707_1659 --workers 6

# engine 雙剖面體檢
python -m engine.scan --profile health --sheet
python -m engine.run_all --sheet --deep-stale

# 每日寄信版
python daily/batch_audit.py --daily          # 排程(分5組輪掃)
python daily/batch_audit.py --only id.taipei --no-mail

# 每月檢核 / 同步設定
python monthly/monthly_check.py --no-ai
python monthly/sync_config.py

# Sheet 連線診斷
python read_sheet.py
```

追蹤整夜稽核進度：看報告目錄的 `progress.json`(每站 flush)，跑完會出現
`summary.json` 與 `CONFIRMED_hijacks.csv`。

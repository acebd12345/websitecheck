# websitecheck 專案架構文件

> 本文件由程式碼全面走讀後整理，供日後回來快速上手。**只描述現況**。
> 最近更新（2026-07）：試算表收斂為 4 分頁（府內網站表為單一母表、主設定表退役）、詞庫/站清單/分頁參數單一來源化、`full_overnight --verify`、`report_html` 報告產生器、CI/CD、Gemini 供應商、局處寄信 Sheet 公式、寄信搬進深掃 `engine/mailer.py`（按局處彙整、吃 AI 複查）、daily 退役、**Phase 2：`monthly_check.py` 退役、合規檢核搬進 `engine/compliance.py`（深掃 worker 整合）、`report_html` 加合規檢核紅綠燈、`update_excel` 改讀 compliance.json、`full_overnight --force-cap`**。

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
| ~~每月檢核~~ | `monthly/` | **主流程已退役** | `monthly_check.py` 已退役(git rm)；檢查邏輯搬進 `engine/compliance.py`（深掃 worker 整合）。`deep_check.py` 保留(被 compliance 匯入)、`update_excel.py` 讀 compliance.json 產 Excel | 檢核表 Excel(由 update_excel 產) |
| ~~每日連結稽核~~ | `daily/` | **已退役** | 掃描引擎 `audit_links.py` 保留；寄信併入 `engine/mailer.py` | — |
| **統一引擎** | `engine/` | 手動 / 整夜 / 排程 | 靜態優先抓取地基 + 全 466 站深度稽核 + **按局處寄信（AI 複查後）** + HTML 報告 | reports/ 下 json/csv + Email |

各子系統**共用同一份設定 `config.py`、同一個 Google 服務帳戶、同一張試算表**。
`engine/` 把 `daily/audit_links.py`（連結稽核引擎）與 `monthly`（AI 判讀、日期抽取、站內深爬）的能力
接起來，疊出「全站深度稽核 + 合規檢核 + 按局處寄信」的完整管線。

### 資料的單一事實來源：Google 試算表「府內網站表」
唯一手動維護的母表（466 站），程式端集中在 `config.SITE_LIST_WS`。試算表共 4 張分頁：

| 分頁 | 角色 |
|---|---|
| **府內網站表** | 站清單母表：網站名稱、網址、局處、`內容抓取方式`、`合規檢核`（是=納入月度合規檢核，可增減，取代舊 `web_check14站`）、`頁數`（自適應回填）、`局處Email`（XLOOKUP 計算欄，見下）|
| **掃描設定** | 詞庫（賭博/色情/停放）與分頁參數，`scan_settings` 啟動時拉取 |
| **局處聯絡人員表** | 局處 → 管理人/分機/Email，寄信 per-局處 用（衛星表，以局處對鍵）|
| **月度掃描排程** | 466 站分 30 天輪替計畫 |

> 原「主設定表」（14 站合規子集）已**併入府內網站表退役**——合規站改由 `合規檢核=是` 旗標標記；欄位重複、兩表漂移的問題消除。

- `monthly/sync_config.py` 讀「府內網站表 篩 `合規檢核=是`」產出：`private/sites.json`（合規站清單）。~~`domains.txt` 產出已移除~~（寄信改由 `engine/mailer.py` 直接讀 Sheet `局處Email` 欄）。
- 大量掃描讀本機快照 `private/TCGweb_466站對照清單_v2.csv`——**`full_overnight` 每次執行前自動從府內網站表重新下載**（`page_budget.refresh_csv`，失敗沿用舊快照），故改站清單/網址/抓取方式**只改 Sheet 即可**，快照只是快取。
- **局處→寄信對照（Sheet 公式，零程式）**：府內網站表的 `局處Email` 欄用 `XLOOKUP` 查局處聯絡人員表，查不到自動落預設信箱；局處聯絡人員表另有偵測公式，即時列出「母表有、聯絡人表沒建」的局處。局處清單只在母表維護，聯絡人表用局處對鍵、漏的會被抓出、寄信永遠有兜底。

---

## 3. 資料夾與檔案結構（逐檔職責）

```
websitecheck/
├─ config.py                 共用設定載入器(private/config.json → 根目錄 → example)
├─ scan_settings.py          掃描設定單一來源(Sheet「掃描設定」→ 快取 → 內建預設;詞庫/分頁參數)
├─ config.example.json       設定範本(公開)
├─ ~~每月檢核.bat~~ (已退役 git rm) / 每日稽核.bat
├─ requirements.txt
│
├─ monthly/   ── 合規檢核輔助（主流程已搬進 engine/compliance.py）──
│   ├─ ~~monthly_check.py~~  已退役(git rm)；檢查邏輯搬進 engine/compliance.py
│   ├─ deep_check.py      站內深度 BFS(≤150頁)：內部失效連結 + Office 檔缺 PDF/ODF 替代版
│   │                     (被 engine/compliance.py import 使用)
│   ├─ node_check.py      檢核表(一) 逐節點 AI 內容判讀
│   ├─ ga_traffic.py      撈 GA4 screenPageViews(檢核表的「網站流量數」)
│   ├─ probe_method.py    探測每站「該用哪種抓取方式」→ 寫回府內網站表「內容抓取方式」欄
│   ├─ sync_config.py     府內網站表(合規檢核=是) → sites.json (domains.txt 產出已移除)
│   ├─ update_excel.py    讀 compliance.json 產檢核表 Excel(以「網站名稱」為鍵比對工作表)
│   ├─ webcheck_ai.py     ★ 共用 AI 模組：fetch_html / html_to_text / ask_ai
│   │                     (支援 openai 相容 / anthropic / gemini 三家，engine 也重用它)
│   ├─ smoke_test.py      部署冒煙測試(不寫檔、不改線上; monthly_check 已從模組清單移除)
│   └─ 每月建立檢核表.gs   Google Apps Script(在試算表端)
│
├─ daily/    ── 連結稽核引擎（寄信已退役，移至 engine/mailer.py）──
│   └─ audit_links.py     ★★ 連結稽核引擎核心。爬單站 → 收外連 → 逐連結判風險。
│                          所有搶註/賭博/色情/失效判斷、誤報防呆都在這支(見 §5)
│
├─ engine/   ── 統一引擎(整併地基) ───────────────────────────
│   (詳見 §3.1，共 12 檔)
│
└─ private/  ── 機敏/個資/產出，整個 gitignore ─────────────
    ├─ config.json / ga-service-account.json
    ├─ sites.json / nodes_map.json
    ├─ TCGweb_466站對照清單_v2.csv   466 站離線快照(engine 大量掃描讀這支)
    └─ reports/…                     各式報告(full_overnight_* / linkaudit_all_* / engine_run_*)
```

### 3.1 engine/ 那 12 個檔

分成三層：**地基（抓取）→ 中層（爬取/清單）→ 上層（驅動/剖面/報告）**。

| 檔 | 層 | 職責 |
|---|---|---|
| `__init__.py` | — | 只放整併說明 docstring，無程式碼 |
| **`fetch_layered.py`** | 地基 | ★ **靜態優先分層抓取**。先靜態抓 → `detect_shell` 判是不是 JS 空殼/frameset/內容過少 → 只有必要且 `allow_render=True` 才升級 Playwright 渲染。硬編兩個陷阱名單：`NEVER_RENDER`(wifi.taipei 渲染會撞 500)、`FORCE_MANUAL`(travel.taipei 有 Cloudflare 人機驗證) |
| `dates.py` | 地基 | 從 HTML 抽「最新更新日期」並正規化(移植自 TCGweb 的日期抽取器) |
| **`crawl.py`** | 中層 | 深層 BFS 爬蟲(`crawl_site`)。每頁走 `fetch_layered`，做 sitemap 優先、內/外連結拆分、同標題+分頁去重、外連狀態檢查、日期抽取。**產健康向的頁面清單**，非搶註判斷 |
| `page_budget.py` | 中層 | 每站頁數自適應：讀/寫府內網站表「頁數」欄。`get_cap`=上次頁數+100；掃完把本次真實頁數寫回(URL 對鍵、只在變大時更新) |
| **`scan.py`** | 上層 | 雙剖面驅動：`health_profile`(最新消息日期/停更判定/抓取方式) + `compliance_profile`(合規檢核=是 的站送地端 AI 判「首頁有無最新消息區塊」)。`load_sites` 支援 Sheet 或 CSV |
| **`compliance.py`** | 上層 | ★ **合規檢核模組**（原 `monthly_check.py` 邏輯搬入）。HTTPS/憑證/HSTS、RWD、站內檢索、無障礙標章、首頁連結失效、站內深爬(`deep_check`)、AI 內容判讀。基本合規 466 站都做（成本近零）；AI+deep_check 只做「合規檢核=是」的站。由 `full_overnight` worker 呼叫，產出 `compliance.json` |
| **`run_all.py`** | 上層 | 日常執行殼：一次跑 健康剖面(全站) → 合規剖面(合規檢核集 AI) → 選擇性深爬(`--deep`/`--deep-stale`)，產綜合摘要 |
| **`full_overnight.py`** | 上層 | ★★ **全 466 站四階段深度稽核**(整夜無人值守) + `--mail` 按局處寄信 + 合規檢核整合(`compliance.py`)。`--force-cap N` 強制所有站首掃上限=N 頁（停用加碼+頁數回寫，環境驗證/淺掃用）。見 §4 |
| **`mailer.py`** | 上層 | 深掃寄信模組：讀報告目錄按局處彙整、吃 AI 複查(C 不寄)、`--mail-to` override 收件人(鐵律)。`full_overnight --mail` 自動呼叫，也可獨立跑 `python -m engine.mailer <報告目錄>` |
| **`report_html.py`** | 上層 | HTML 報告產生器：把掃描產出轉成單站報告(按局處歸資料夾)＋全市總報告。吃 AI 複查判定(C→附錄、A/B→置頂)；新增**合規檢核**區塊(紅綠燈表格，讀 compliance.json)。`full_overnight` 收尾自動呼叫，也可獨立跑 `python -m engine.report_html` |
| ~~`verify_suspicious.py`~~ | — | **已移除**（2026-07，功能由 `full_overnight --verify` 取代） |

---

## 4. 端到端流程：資料怎麼流動

### 4.1 全站深度稽核（主線，`full_overnight.py` 四階段 + 寄信）

這是專案最核心的資料流。輸入 466 站 CSV，輸出「確認的搶註/掛馬清單」+ 按局處寄信。

```
府內網站表/CSV (466 站，含 內容抓取方式、頁數)
   │  csv.DictReader，套 --org/--only/--limit 過濾
   │  method∈{manual,疑似失效} 或 3d.taipei → 跳過(只記錄不掃)
   ▼
【階段1】站層級多行程平行(ProcessPoolExecutor, 每站獨立行程 max_tasks_per_child=1)
   每站 audit_one：
     ALLOW_RENDER = (method=="playwright")         ← 渲染只給 playwright 站
     首掃上限 = page_budget.get_cap(頁數欄+100)    (--force-cap N 時全用 N、停用加碼)
     ★ compliance.run_checks(站)                  ← 合規檢核(HTTPS/RWD…)整合進每站 worker
     audit_links.audit_site(url, cap):
        crawl_internal  BFS 爬站內頁(≤8 執行緒)，收集所有【對外】連結 + 出現位置
                        分頁參數(page/date/…)不再往下挖，避免月曆無限頁
        check_external  每條外連逐一判風險(見 §5 判斷樹)
     撞上限 → 加碼 1000→3000→6000→9000 封頂，直到爬完或到頂
     爬到 0 頁 → 開渲染重試 ≤2 次(SPA 空殼保護)
   邊完成邊 flush：all_problems.csv(所有異常) + progress.json(每站進度)
   收尾：把本次各站真實頁數 write_sheet 寫回府內網站表「頁數」欄
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

### 4.2 按局處寄信（`engine/mailer.py`，深掃階段4後）

深掃跑完後，`--mail` 觸發 `engine/mailer.py`：
1. 讀 `all_problems.csv` + `suspicious_verified.csv`，按局處分組
2. SUSPICIOUS 依 AI 複查判定過濾：**A/B 才列入信、C(誤報)不寄、?(待人工)列信末備註**
3. 一個局處若複查後 0 條真問題 → 不寄（避免空信轟炸）
4. 收件人：預設走各局處 `局處Email` 真值(per-局處，從 466 站 CSV 讀取)；`--mail-to` override 可指定(測試用)；查無 Email 時 fallback→config `mail_override_to`，再無則跳過(不得寄錯人)
5. 一封信含該局處所有異常站、HTML 版型 + CSV 附件、主旨含局處名+異常數（A 判定加「【急】」）

可獨立對既有報告補寄：`python -m engine.mailer <報告目錄> [--mail-to ...] [--dry-run]`

### 4.3 合規檢核（`engine/compliance.py`，整合進深掃 worker）

> `monthly_check.py` 已退役(git rm)。其檢查邏輯搬進 `engine/compliance.py`，由 `full_overnight` 的每站 worker 呼叫，不再獨立執行。

**檢查項目**（同原 monthly_check）：HTTPS 憑證/HSTS、RWD、站內檢索、無障礙標章、首頁連結失效、站內深爬(`monthly/deep_check.py`)、AI 內容判讀。

**分級執行**：基本合規（HTTPS/RWD/檢索/無障礙）466 站都做（成本近零）；AI 判讀 + deep_check 只做「合規檢核=是」的站。

**產出**：報告目錄下的 `compliance.json`（各站合規結果），被 `update_excel.py` 讀取產 Excel 檢核表、被 `report_html.py` 讀取產合規檢核紅綠燈區塊。

### 4.4 engine 雙剖面（`run_all.py` / `scan.py`）

健康剖面(全站首頁層、靜態優先、快) + 合規剖面(合規檢核集送 AI)，可選深爬停更站。
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
- 每站上次真實頁數記在府內網站表「頁數」欄；下次首掃上限 = 該值 + 100(`BUFFER`)。
  掃完只在「這次爬更多」時把值寫大 → 記錄各站最大規模，且分頁跳過讓陷阱頁不灌大這個值。

### (D) 站層級多行程平行 + 逐項 flush
每站一個獨立子行程(`max_tasks_per_child=1`)：`ALLOW_RENDER`/渲染額度各自獨立不互相干擾，
且每站跑完就釋放記憶體。所有產出邊完成邊 flush 寫檔，中途可查 progress、可 `--resume` 續跑。

### (E) 0 頁站保護
掃出 0 頁有兩種：暫時性抓空(重跑會恢復) vs SPA 空殼(需渲染)。`audit_one` 對 0 頁站
開渲染重試 ≤2 次——靜態有料者重試仍走靜態即成功，SPA 空殼者這次被渲染救回。

### (F) AI 供應商可換（`webcheck_ai`）
同一套 `ask_ai` 依 `config.ai_provider` 切 openai 相容(地端 vLLM/Ollama 或雲端) / anthropic / gemini。
地端跑(自架 AI 端點)可讓個資不出機關；雲端則裝 SDK 即用。

---

## 6. 技術債 / 重複 / 可清理處

> 以下為走讀觀察，**非本次要動的項目**，供日後重構參考。按影響程度排序。

### 高：重複的「同一件事的不同版本」
1. ~~**全站連結稽核有兩份**：`engine/link_audit_all.py`~~ **已移除**（2026-07，功能完全被 `full_overnight.py` 階段1 涵蓋）。
2. ~~**SUSPICIOUS 第二關有兩份**~~：**已解決**（2026-07，`verify_suspicious.py` 已移除，功能由 `full_overnight --verify` 取代）。
3. ~~**賭博/色情關鍵字清單有兩份**~~：**已解決**（2026-07，統一為 `scan_settings.py` 單一來源，Sheet「掃描設定」可調）。

### 中：三個 BFS 爬蟲、多條 HTML→text 路徑
- 專案裡有**三個爬蟲**各為其目的：`audit_links.crawl_internal`(外連導向, requests+BS4)、
  `engine/crawl.crawl_site`(健康/日期導向, urllib+fetch_layered)、`monthly/deep_check`(站內失效導向)。
  三者都做 BFS + 抽連結 + 分頁跳過，但去重與外連處理各異。短期難合併(目的不同)。
  ~~`PAGINATION_PARAMS` 在 `audit_links` 與 `engine/crawl` 各定義一份且集合不同，易漂移，可先統一。~~
  **已解決**（2026-07，統一為 `scan_settings.get("pagination_params")`，16 參數完整版含月曆類）。
- HTML 轉文字散在 `webcheck_ai.html_to_text`、`audit_links` 的 BS4 `get_text`、
  `full_overnight.visible_text` 三處。

### 中：硬編絕對路徑，不可攜
- `engine/*` 幾乎每支開頭 `sys.path.insert(0, r"D:\websitecheck")`，且 `CSV_LIST` /
  `CSV_DEFAULT` 把 `D:\websitecheck\private\TCGweb_466站對照清單_v2.csv` 寫死在 3 個檔。
  → 專案已有 `config.BASE_DIR`/`config.PRIVATE_DIR`，engine 卻沒用；換機器或改路徑會全斷。
  建議統一改讀 config。（`verify_suspicious` 的 glob 路徑同樣寫死。）

### 低：報告目錄無保留策略
- `private/reports/` 已累積多個 `full_overnight_*` 目錄，多是 1~2 站的定點重測。
  reports 沒有自動清理。建議加保留天數/自動歸檔。

### 低：跨行程 print 編碼
- 子行程內 `audit_links` 的 crawl 進度 `print` 在被重導到 log 時會呈現 Big5 亂碼
  （`full_overnight` 自己的 `[階段x]` 行有 `reconfigure(utf-8)` 是乾淨的）。不影響結果，追 log 時知道即可。

---

## 7. 常用指令速查

```bash
# 全站四階段深度稽核(整夜) + 按局處寄信
python -m engine.full_overnight --max-pages 8000 --workers 6 --mail
# 只掃某局處 / 定點重測 / 續跑
python -m engine.full_overnight --org 教育局 --workers 6
python -m engine.full_overnight --only taitraesource --workers 3
python -m engine.full_overnight --org 教育局 --resume full_overnight_20260707_1659 --workers 6
# 複查模式:不重爬,對既有報告重跑階段2-4(取代舊 verify_suspicious)
python -m engine.full_overnight --verify <報告目錄>

# 對既有報告補寄 / dry-run 確認
python -m engine.mailer <報告目錄> --dry-run
python -m engine.mailer <報告目錄> --mail-to <收件人>

# HTML 報告產生(單站+全市,吃AI複查降級)
python -m engine.report_html                      # 全部:所有站+全市總報告
python -m engine.report_html --org 教育局          # 只產某局處
python -m engine.report_html --city --zip          # 只產全市總報告+壓zip

# engine 雙剖面體檢
python -m engine.scan --profile health --sheet
python -m engine.run_all --sheet --deep-stale

# 每月檢核 / 同步設定
python monthly/monthly_check.py --no-ai
python monthly/sync_config.py
```

追蹤整夜稽核進度：看報告目錄的 `progress.json`(每站 flush)，跑完會出現
`summary.json` 與 `CONFIRMED_hijacks.csv`。

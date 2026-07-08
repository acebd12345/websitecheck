# 交接筆記（給 Gemini／下一個接手的 agent）

> 這份是本輪工作累積的「記憶」——操作地雷、分析結論、目前狀態、待辦。
> 雲端化的**執行規格**另見 [HERMES_HANDOFF.md](HERMES_HANDOFF.md)；系統全貌見 [ARCHITECTURE.md](ARCHITECTURE.md)。
> 使用者會自己先審這份，再決定交給 Gemini 跑。撰寫日：2026-07-08。

---

## A. 操作地雷（一定要知道，否則會卡）

1. **git push 只能由使用者在本機終端機手動做。**
   這個自動化/headless 環境一 push 就卡在 Windows Git Credential Manager (GCM) 的登入視窗、逾時被中止。
   agent 能做的：`git add`/`commit`、用 `GIT_TERMINAL_PROMPT=0 git ls-remote origin main` 複驗遠端 SHA；
   **不能 push**。流程 = agent commit 完 → 請使用者跑 `git push origin main` → agent 用 ls-remote 複驗。

2. **寫 Google Sheet 一律「新增分頁、非破壞」。** 不覆蓋、不刪除既有分頁或欄位。
   若要寫每站資料，**用「網址」對鍵、逐列對準，不可按列序**（既有鐵律）。
   寫新分頁前先 `sh.worksheets()` 檢查同名是否已存在，存在就跳過不覆寫。

3. **機敏一律走 private/（已整包 gitignore）或環境變數。**
   絕不 commit `config.json`、`ga-service-account.json`；**內部 AI 主機名**（存 `private/config.json` 的 `ai_base_url`）
   不可出現在任何 tracked 檔。公開前掃：`git ls-files -z | xargs -0 grep -InE "<主機名>|@gov.taipei|AKIA|ghp_|-----BEGIN"`、`git ls-files private/`（須空）。
   `config.py` 每個 key 可被同名大寫環境變數覆寫 → 雲端就靠這個注入，不需上傳 config.json。

---

## B. 分析結論（省得重跑）

4. **「同 host 多語言版」要分兩種邏輯處理。**
   孔廟儒學文化網(中/英/日/韓，同 host `www.tctcc.taipei`，只差路徑 `/`、`/en-us/`、`/jp/`、`/ko/`)、
   投資服務辦公室(中`/` + 英`/en/`)、臺北大縱走(中/英/日/韓) 等都是「同網域語言分身」。
   - **深掃連結稽核**：可 dedup，爬中文一筆即涵蓋全語言（爬蟲只認 host、會沿語言切換連結跑遍）→ 省大量重複抓取（光孔廟 ~27,000 次）。
   - **停更判定**：**不能 dedup**，各語言的「最新消息」日期各自獨立（實測孔廟中文 2026-06 有更新、日文 2025-05、韓文 2024-05 都停更）。

5. **時間模型（實測）**：每頁爬取 ~0.27s（6 workers）、每筆地端 AI 複查 ~3s。
   **AI 只佔總時間 1–3%，瓶頸是頁數，尤其單一巨站**（worker 平行救不了站內）。
   6 個巨站各 ≥9000 頁（撞 CEIL 上限，實際更大）：i-Voting、孔廟中/英/日/韓、採購業務資訊網。
   注意：AI 複查對「死域名候選」會重試 3×(20s timeout+4s)，單筆最壞 ~70s（是 fetch timeout 不是 AI 慢）。

6. **「內容抓取方式」欄的 `ai` 在深掃無特殊作用**：深掃只認 `playwright`（開渲染），`ai`/`code` 都走靜態。
   `ai` 是「每月檢核用地端 AI 判讀內容」的標記，對 full_overnight 深掃 == `code`。

7. **軟 404 / 登記網址過期 = 假失效**：站活著、但清單登記的深層網址 404（改版/反爬）。
   13 個 `疑似失效` 複查後 = **4 假失效（改網址即可）+ 9 真失效（DNS掛/服務下線）**。
   假失效修法（純資料）：投資辦→`https://invest.taipei/`、投資辦英→`https://invest.taipei/en/`、
   臺北e大→`https://elearning.taipei/`、獎勵補助英→待確認。

8. **停更盤點**：>5 個月未更新 = 44 站（有 Sheet 日期，可信）+ 47 站（靜態補判，**部分不可信**）。
   **英文版網站是重災區**（英/日/韓外語版一再停更）。
   ⚠ 靜態補判裡「很老的日期」（2002 北投溫博、2014 秘書處英文、2020 台北通…）多半是**頁尾版權年或需渲染站**，
   不是真的最新消息，要用渲染版重驗，別當定論。

---

## C. 目前狀態（已完成、已上 GitHub）

- repo：https://github.com/acebd12345/websitecheck ，`main` = 最新（本輪全部已推）。
- **文件**：`docs/ARCHITECTURE.md`（架構）、`SECURITY_CLOUD.md`（資安）、`HERMES_HANDOFF.md`（雲端規格）、
  `cloud/`（掃描設定分頁設計、30天排程、失效清單、未更新清單、補判結果）、本檔。
- **Google Sheet**（`TaipeiCityGovWebsiteCheck`）：既有 `主設定表`(14站)、`TCGweb466站清單`(466站主資料)；
  本輪新增 4 分頁 → `掃描設定`、`月度掃描排程`、`失效網站清單`、`未更新網站清單`。
- **程式清理**：已刪 `engine/link_audit_all.py`、`read_sheet.py`（死碼/重複）；保留 `verify_suspicious.py`。

---

## D. 待辦（Gemini 可接著做）

1. **雲端化**：照 `HERMES_HANDOFF.md` 的 7 步驟。第一步必做 = 把 `engine/*` 的硬編絕對路徑
   （`sys.path.insert(0, r"D:\websitecheck")`、CSV 路徑）改讀 `config.BASE_DIR`/env，否則換機器會斷。
2. **停更盤點補完**：24 個「需渲染」站要開 playwright 才拿得到真實更新日；47 個靜態補判站需渲染版覆驗。
3. **清單資料修正**（承辦決策後）：4 假失效站改網址、9 真失效站是否除役、英文版停更通報各局處。
4. **巨站拆時段**（可選新功能）：`crawl_internal` 目前只認 host，若要按路徑分段掃巨站需加 path-scope 能力。
5. **消除重複**（技術債）：`SUSPICIOUS_KEYWORDS` vs `BAD_KW` 兩份關鍵字清單、多條 HTML→text 路徑，見 ARCHITECTURE §6。

---

## E. 給 Gemini 的注意事項
- 這是正式、承辦共用的政府系統 + 試算表；任何寫入前先確認、非破壞、可回復。
- 涉及「刪除/移除」一律先列清單給人看，不自行刪（失效站、清單瘦身都是資料治理決策）。
- 地端 AI 端點（`private/config.json` 的 `ai_base_url`）可保留地端跑（個資不出機關）或改雲端，見 SECURITY_CLOUD。

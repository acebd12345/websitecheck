# web_check — 政府網站檢核整合工具

臺北市政府 466 個網站的自動化檢核。三個子系統整合在同一專案、共用一份設定與服務帳戶：

| 子系統 | 資料夾 | 頻率 | 做什麼 | 產出 |
|---|---|---|---|---|
| **monthly** 每月檢核 | `monthly/` | 每月（本機） | HTTPS/RWD/連結/站內深度爬檢、GA流量、AI內容判讀 | 檢核表 Excel + 報告 |
| **daily** 對外連結稽核 | `daily/` | 每日（主機 cron） | 全站爬取，查連結失效/網域被搶註/賭博色情 | 逐站寄信 + CSV |
| **engine** 統一引擎 | `engine/` | 手動 / 整夜 | 靜態優先抓取地基，長出健康剖面、合規剖面、**全 466 站四階段深度稽核** | reports/ 下 summary/CONFIRMED_hijacks 等 |

三者共用：一份 `config.json`、一個 GA 服務帳戶金鑰、同一張 Google 試算表——唯一手動維護的是「主設定表」分頁。monthly 直接讀它；daily 讀的是 `sync_config` 從主設定表產生的 `private/domains.txt`；engine 讀 `TCGweb466站清單` 分頁（含每站「頁數」欄，掃完自動回填）。

> 完整架構、資料流、設計決策與技術債見 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**。

## 一鍵 / 常用執行

- **每月檢核**：雙擊 `每月檢核.bat`（同步設定 → 檢測 → 產檢核表 → AI判讀）
- **對外連結稽核**：雙擊 `每日稽核.bat`（本機測試），或主機 cron 跑 `daily/run_daily.sh`
- **全站深度稽核**（找搶註/掛馬）：`python -m engine.full_overnight --workers 6`
  - 只掃某局處：`--org 教育局`；定點重測：`--only <關鍵字>`；中斷續跑：`--resume <報告目錄>`
- **站況體檢**（更新時效/停更）：`python -m engine.scan --profile health --sheet`

## 安裝

```bash
pip install -r requirements.txt
```

## 設定（集中在 private/，整個 gitignore）

1. 複製 `config.example.json` → `private/config.json`，填入：
   - AI 供應商（`ai_provider`：openai / anthropic / gemini）
   - `master_sheet_id`：Google 試算表 ID
   - `mail`/`gmail`：daily 寄信方式（outlook 或 gmail SMTP）
   - `scan`：daily 掃描參數（max_pages、白名單、免檢名單）
2. GA 服務帳戶金鑰命名 `ga-service-account.json` 放進 **`private/`**
3. 首次建節點對照表：`cd monthly && python node_check.py map`

> 服務帳戶需有試算表編輯權；金鑰同時供 monthly（撈 GA）與 daily（讀清單）使用。

### AI 供應商（monthly 內容判讀）

| `ai_provider` | 走法 | `ai_model` 範例 | 需裝套件 |
|---|---|---|---|
| `openai` | OpenAI 相容 HTTP（地端 vLLM/Ollama，或 OpenAI/OpenRouter/Groq）| `gpt-4o-mini` | 無 |
| `anthropic` | 官方 Claude SDK | `claude-opus-4-8` / `claude-haiku-4-5` | `anthropic` |
| `gemini` | Gemini OpenAI 相容端點 | `gemini-2.5-flash` | 無 |

### daily 寄信

- `method: outlook`：本機 Windows + Outlook（測試期）
- `method: gmail`：Linux 主機 SMTP，需在 `gmail.app_password` 填 Google 應用程式密碼
- 主機排程：`crontab -e` 加 `0 3 * * * /path/web_check/daily/run_daily.sh`
- 詳見 `daily/排程部署說明.txt`

## 資料夾結構

```
web_check/
├─ config.py / config.example.json      共用設定
├─ scan_settings.py                     掃描設定單一來源(Sheet→快取→內建預設;詞庫/分頁參數)
├─ 每月檢核.bat / 每日稽核.bat            兩個一鍵入口
├─ monthly/   每月檢核程式
├─ daily/     對外連結稽核程式（audit_links 引擎 + run_daily.sh/.bat）
├─ engine/    統一引擎（full_overnight 四階段深掃、scan/run_all 雙剖面、fetch_layered 分層抓取…）
├─ docs/      架構與雲端化文件（見下）
└─ private/   機敏、個資、產出（gitignore）
   ├─ config.json / ga-service-account.json
   ├─ domains.txt / sites.json / nodes_map.json / TCGweb_466站對照清單_v2.csv
   ├─ reports/ / 檢核表/ / logs/
   └─ problems_*.csv / links_*.jsonl（daily 產出，monthly 會讀來併入報告）
```

## 文件（`docs/`）

| 文件 | 內容 |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系統全貌、資料流、關鍵設計決策、技術債 |
| [SECURITY_CLOUD.md](docs/SECURITY_CLOUD.md) | 資安清單／上雲機敏處理指引（哪些不可公開、怎麼注入） |
| [HERMES_HANDOFF.md](docs/HERMES_HANDOFF.md) | 雲端化交接規格（給接手的 agent 照做） |
| [cloud/](docs/cloud/) | 掃描設定分頁設計、30 天排程、失效清單、未更新清單等雲端化產出 |

## 授權

MIT（見 LICENSE）。內含之機關特定設定、個資、金鑰均在 `private/`，不在此 repo。

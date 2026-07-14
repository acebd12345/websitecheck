# web_check — 政府網站檢核整合工具

臺北市政府 466 個網站的自動化檢核。三個子系統整合在同一專案、共用一份設定與服務帳戶：

| 子系統 | 資料夾 | 頻率 | 做什麼 | 產出 |
|---|---|---|---|---|
| **monthly** 每月檢核 | `monthly/` | 每月（本機） | ~~monthly_check 已退役~~；合規檢核搬入 `engine/compliance.py` | 檢核表 Excel（讀 `compliance.json`） |
| ~~daily~~ | `daily/` | **已退役** | 掃描引擎 `audit_links.py` 保留；寄信併入 engine | — |
| **engine** 統一引擎 | `engine/` | 手動 / 整夜 / 排程 | 靜態優先抓取地基 + 全 466 站四階段深度稽核 + **按局處寄信（AI 複查後）** + HTML 報告 | reports/ + Email |

兩者共用：一份 `config.json`、一個 GA 服務帳戶金鑰、同一張 Google 試算表。唯一手動維護的母表是 **「府內網站表」**（466 站，`config.SITE_LIST_WS`）；合規檢核由 `engine/compliance.py` 產出 `compliance.json`，monthly 讀該 JSON 填表、engine 讀府內網站表（含每站「頁數」欄自動回填、`局處Email` XLOOKUP 對照欄）。詞庫/分頁參數在「掃描設定」分頁、寄信收件在「局處聯絡人員表」。

> 完整架構、資料流、設計決策與技術債見 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**。

## 一鍵 / 常用執行

- **合規掃描**：`python -m engine.compliance`（產 `compliance.json`，供檢核表填寫）
- **排程深掃+寄信**（每晚只掃今天那批）：`python -m engine.full_overnight --schedule-today --mail --workers 6`
- **全站深度稽核+寄信**（找搶註/掛馬，全量）：`python -m engine.full_overnight --workers 6 --mail`
  - 只掃某局處：`--org 教育局`；定點重測：`--only <關鍵字>`；中斷續跑：`--resume <報告目錄>`
  - 對既有報告補寄：`python -m engine.mailer <報告目錄> [--dry-run]`
- **HTML 報告產生**（單站+全市，吃 AI 複查降級）：`python -m engine.report_html --zip`
- **站況體檢**（更新時效/停更）：`python -m engine.scan --profile health --sheet`
- **排程管理**：`python -m engine.schedule --rebuild`（重算30天分批）、`--today`（印今天那批）

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

### 寄信（engine/mailer.py，深掃後按局處寄）

- `method: outlook`：本機 Windows + Outlook
- `method: gmail`：Linux 主機 SMTP，需在 `gmail.app_password` 填 Google 應用程式密碼
- 深掃加 `--mail` 自動寄，收件人自動讀府內網站表「局處Email」欄（查無 Email 時 fallback 到 config `mail_override_to`；`--mail-to` 僅測試用 override）

## 資料夾結構

```
web_check/
├─ config.py / config.example.json      共用設定
├─ scan_settings.py                     掃描設定單一來源(Sheet→快取→內建預設;詞庫/分頁參數)
├─ 每月檢核.bat / 每日稽核.bat            兩個一鍵入口
├─ monthly/   每月檢核程式
├─ daily/     連結稽核引擎（audit_links.py 保留；寄信已移至 engine/mailer.py）
├─ engine/    統一引擎（full_overnight 四階段深掃、compliance.py 合規檢核、scan 停更剖面、fetch_layered 分層抓取…）
├─ docs/      架構與雲端化文件（見下）
└─ private/   機敏、個資、產出（gitignore）
   ├─ config.json / ga-service-account.json
   ├─ sites.json / nodes_map.json / TCGweb_466站對照清單_v2.csv
   ├─ reports/ / 檢核表/ / logs/
   └─ reports/full_overnight_*/ 各式報告(含 mail_*.csv 寄信附件)
```

## 文件（`docs/`）

| 文件 | 內容 |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系統全貌、資料流、關鍵設計決策、技術債 |
| [SECURITY_CLOUD.md](docs/SECURITY_CLOUD.md) | 資安清單／上雲機敏處理指引（哪些不可公開、怎麼注入） |

## 授權

MIT（見 LICENSE）。內含之機關特定設定、個資、金鑰均在 `private/`，不在此 repo。

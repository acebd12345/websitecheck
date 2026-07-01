# web_check — 政府網站檢核整合工具

兩套網站檢核工具整合在同一專案、共用一份設定與服務帳戶：

| 子工具 | 資料夾 | 頻率 | 做什麼 | 產出 |
|---|---|---|---|---|
| **monthly** 每月檢核 | `monthly/` | 每月（本機） | HTTPS/RWD/連結/站內深度爬檢、GA流量、AI內容判讀 | 檢核表 Excel + 報告 |
| **daily** 對外連結稽核 | `daily/` | 每日（主機 cron） | 全站爬 8000 頁，查連結失效/網域被搶註/賭博色情 | 逐站寄信 + CSV |

兩者共用：一份 `config.json`、一個 GA 服務帳戶金鑰、同一張 Google 試算表——唯一手動維護的是「主設定表」分頁。monthly 直接讀它；daily 讀的是 `sync_config` 從主設定表產生的 `private/domains.txt`。

## 一鍵執行

- **每月檢核**：雙擊 `每月檢核.bat`（同步設定 → 檢測13站 → 產檢核表 → AI判讀）
- **對外連結稽核**：雙擊 `每日稽核.bat`（本機測試），或主機 cron 跑 `daily/run_daily.sh`

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
├─ 每月檢核.bat / 每日稽核.bat            兩個一鍵入口
├─ monthly/   每月檢核程式
├─ daily/     對外連結稽核程式（+ run_daily.sh/.bat 排程入口）
└─ private/   機敏、個資、產出（gitignore）
   ├─ config.json / ga-service-account.json
   ├─ domains.txt / sites.json / nodes_map.json
   ├─ reports/ / 檢核表/ / logs/
   └─ problems_*.csv / links_*.jsonl（daily 產出，monthly 會讀來併入報告）
```

## 授權

MIT（見 LICENSE）。內含之機關特定設定、個資、金鑰均在 `private/`，不在此 repo。

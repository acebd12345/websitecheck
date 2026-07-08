# 資安清單 / 上雲機敏處理指引

> 目的：上雲（git 公開 or 部署到雲端）前，明確界定「絕對不可進版控 / 不可公開」的東西，
> 並指定每項在雲端的正確存放方式。搭配 `docs/HERMES_HANDOFF.md` 一起看。
> 對應現況：`private/` 已整包 gitignore，本 repo `git ls-files` 已驗證不含任何機敏檔。

## A. 絕對不可進版控 / 不可公開

| 項目 | 內容 | 現況是否已 gitignore | 上雲存放方式 |
|---|---|---|---|
| **Service Account 金鑰** | `private/ga-service-account.json`（Google 服務帳戶私鑰，可讀寫試算表、撈 GA） | ✅ `private/` 已排除 | Secret Manager；雲端以 workload identity 或掛載密鑰檔，路徑用 env `GA_KEY_FILE` |
| **AI 端點金鑰** | `config.json.ai_api_key` | ✅（在 private/config.json） | env `AI_API_KEY` / Secret Manager |
| **Gmail App Password** | `config.json.gmail.app_password`（寄信用應用程式密碼） | ✅ | env `GMAIL_APP_PASSWORD` / Secret Manager |
| **Google Sheet ID** | `config.json.master_sheet_id` ＋ 分頁 gid | ✅ | env `MASTER_SHEET_ID`（非機密等級高，但不公開為宜） |
| **內部 AI 主機名** | `ai_base_url`＝地端 AI 主機（實際主機名僅存 `private/config.json`，本文件不重述） | ✅（僅存 private/config.json） | env `AI_BASE_URL`；勿寫入任何公開檔或 Sheet |
| **承辦人 PII** | 主設定表/466清單的 `填表人`、`分機`、`Email` 欄 | ✅（資料在 Sheet，非 repo） | 保留在 Sheet（存取受 Google 權限控管）；**勿匯出到公開 repo/報告** |
| **稽核收件人信箱** | `稽核收件人`、`副本` 欄、`domains.txt` | ✅（`private/domains.txt` 已排除） | 保留在 Sheet / Secret；報告輸出時遮罩 |
| **內網位址 / 主機** | 任何 `10.*`/`192.168.*`/內部 hostname | ✅（目前 tracked 檔已掃無） | 只進 env / 內部設定，不進 repo |
| **產出資料** | `private/reports/`、`檢核表/`、`logs/`、`problems_*.csv`、`links_*.jsonl` | ✅ `private/` + 副檔名規則 | 雲端寫物件儲存（GCS/S3）私有 bucket，不進 git |

## B. 可公開（已在 repo，確認無虞）
- 全部**程式碼**（`engine/`、`daily/`、`monthly/`、`config.py`）— 只讀設定、不含硬編密鑰（已掃描確認）。
- `config.example.json` — 佔位符範本，無真值。
- `README.md`、`docs/`（含本文件；注意 docs 內**不得**再出現內部主機名/PII）。
- `LICENSE`、`.gitignore`、`.gitattributes`、`requirements.txt`。

## C. 現況驗證結果（推送前已做）
- `git check-ignore private/` → 命中，`private/` 整包排除。
- `git ls-files private/` → **0 檔**被追蹤。
- tracked 檔密鑰 pattern 掃描 → 僅「從 config 讀值」的程式碼，**無硬編密鑰**。
- 唯一被追蹤的設定檔 = `config.example.json`（範本）。
- 已從 `docs/ARCHITECTURE.md` 抹除內部 AI 主機名（commit 81bb762），並確認本 docs 各檔不再出現該主機名全稱。

## D. 上雲注入原則（給部署）
1. **一切機敏走環境變數**：`config.py` 的 `get()` 已支援「同名大寫 env 覆寫」→ 雲端設
   `AI_BASE_URL / AI_MODEL / AI_API_KEY / MASTER_SHEET_ID / GA_KEY_FILE / GMAIL_APP_PASSWORD` 即可，**不需**上傳 `config.json`。
2. **Service Account 最小權限**：僅授「該試算表編輯」+「GA 讀取」，不要給整個專案 Owner。
3. **金鑰輪替**：Gmail app_password / AI key 定期輪替；Secret Manager 版本化。
4. **產出私有化**：reports/檢核表 一律寫私有 bucket，設定生命週期自動過期（呼應 `log_keep_days`）。
5. **公開前再掃一次**：任何要 public 的 commit，先跑 `git ls-files | xargs grep` 掃「內部 AI 主機名全稱」/`@gov.taipei`/`AKIA`/`ghp_`/`-----BEGIN` 等 pattern（實際主機名字串見 `private/config.json`，掃描時用該值，不要把它寫進任何公開檔；見 HERMES_HANDOFF 附指令）。

## E. 待決策（需承辦確認，非我可代決）
- 這個 GitHub repo 最終是 **public 還是 private**？若 public，`master_sheet_id`、局處清單等雖非密鑰，仍建議評估是否要留在公開程式碼註解/文件中。
- 失效網站（見 `dead-sites.md`）是否從清單移除 → 屬資料治理決策，本工具只列不刪。

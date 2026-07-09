# 報告產生器交辦規格（給 terminal Claude 執行）

> 本文件是完整的實作規格，由前期討論定案，**使用者已審閱核准，照做即可，不需再向使用者確認方案**。
> 鐵律（不 push、Sheet 非破壞、repo 是 public、UTF-8）同 [TECHDEBT_HANDOFF.md](TECHDEBT_HANDOFF.md) §1。
> 系統全貌見 [ARCHITECTURE.md](ARCHITECTURE.md)。撰寫日：2026-07-09。

---

## 0. 背景與目標

掃描引擎（`engine/full_overnight.py` 深掃、`daily/batch_audit.py` 每日寄信版）只產 CSV/JSON 資料檔。
`private/` 裡的「○○局連結稽核報告.html」「全市連結稽核總報告.html」是先前 AI session **手工**做的一次性產物，
且有已知缺陷：明細沒吃 AI 複查降級（機械誤報直接上報告）。

本次交付 **`engine/report_html.py`**：把掃描產出自動轉成人可讀的 HTML 報告。兩種產物、兩種受眾：

| 產物 | 單位 | 受眾 | 說明 |
|---|---|---|---|
| **單站報告** | 一站一份 HTML | 各局處承辦 | 排程輪掃到哪站就重產哪站；按局處歸資料夾，可 zip 打包 |
| **全市總報告** | 一份 HTML | **資訊局內部** | 彙整「每日＋每月排程掃描」的既有產出，隨時可重產 |

**明確不做**（已決策，勿擴大範圍）：
- **不產 PDF**（承辦要 PDF 自己用瀏覽器列印）。
- **不寄信**（第一版只產檔案＋zip；寄送與雲端化另案）。
- 不改動掃描引擎的判定邏輯、不重掃任何網站——本工具**只讀**既有產出。
- 不用 jinja2 等新套件——比照 `batch_audit.build_mail_html` 用純 f-string 組 HTML，零新依賴。

---

## 1. 產出結構

```
private/reports_html/
├─ 兵役局/
│   ├─ 臺北市兵役局.html            ← 單站報告(檔名=站名,見 §4 檔名清洗)
│   └─ 臺北市兵役局英文版.html
├─ 教育局/
│   └─ ...
├─ 其他/                            ← 局處欄空白的站
├─ 兵役局.zip                       ← --zip 時產(整個局處資料夾壓縮)
└─ 全市連結稽核總報告.html
```

全在 `private/` 下（已整包 gitignore），路徑一律經 `config.PRIVATE_DIR` 組出，不寫死絕對路徑。

---

## 2. 資料來源與彙整規則（本工具的核心）

### 2.1 深掃（主要來源）

掃 `private/reports/full_overnight_*/` 全部目錄，每目錄讀：

| 檔 | 用途 |
|---|---|
| `progress.json` | 每站：url、name、org、status、pages、links(外連數) |
| `all_problems.csv` | 異常明細（欄位:site_name,org + risk,url,host,dns,status,final_url,title,note,occurrences,found_on,anchor,all_locations…） |
| `suspicious_verified.json` 或 `.csv` | AI 複查結果（ai_verdict A/B/C/?、type、ai_reason、evidence） |

**每站取最新**：同一站(URL 對鍵)可能出現在多個目錄（定點重測、續跑、月度輪掃），
以**目錄時間戳最新且該站 status=ok** 的那次為準；該站的異常明細只取自那一個目錄。
目錄時間戳從目錄名 `full_overnight_YYYYMMDD_HHMM` 解析，解析不到就用 mtime。

**AI 複查判定套用**（誤報治理，本工具最重要的價值）：
`all_problems` 裡 `risk=SUSPICIOUS` 的列，以 URL join 全部目錄的 `suspicious_verified`（取最新判定）：
- `A` → 顯示 🔴「確認賭博/色情/掛馬」
- `B` → 顯示 🟠「停放/搶註嫌疑」
- `C` → **從主表剔除**，移到附錄「已排除誤報」（附 AI 理由）
- `?` → 顯示 ❓「待人工確認」
- join 不到（沒複查過）→ 顯示 ⚪「機械判定，未複查」

### 2.2 每日寄信版（次要來源，只進全市報告）

掃 `private/problems_{host}_{YYYY-MM-DD}.csv`（`batch_audit` 產出，檔名含日期）。
全市總報告闢一區「每日稽核近況（近 14 天）」：列異常摘要，標明「機械判定、已逐站寄信」。
其中 SUSPICIOUS 若 URL 在深掃的 verified 裡有判定，沿用同一套 A/B/C 呈現規則（C 同樣剔除進附錄）。

### 2.3 局處歸屬

優先用 `all_problems.csv`/`progress.json` 的 `org` 欄；空值站歸「其他」。
站名/局處也可對照 `private/TCGweb_466站對照清單_v2.csv`（欄位：網站名稱、網址、局處）。

---

## 3. 指令介面

```bash
python -m engine.report_html                      # 產全部:所有有資料的站 + 全市總報告
python -m engine.report_html --site 兵役          # 只產名稱/網址含關鍵字的站(逗號分隔)
python -m engine.report_html --org 教育局         # 只產某局處的站
python -m engine.report_html --city               # 只產全市總報告
python -m engine.report_html --zip                # 產完後把每個局處資料夾壓成 {局處}.zip
python -m engine.report_html --days 30            # 每日稽核近況的回看天數(預設14)
```

**掛接深掃**：`full_overnight.py` 收尾（階段4 之後）自動對**本次 status=ok 的站**呼叫單站報告產生
（import `engine.report_html` 的函式，不是 subprocess），並加 `--no-report` 旗標可關閉。
失敗要 try/except 包住印警告——報告產生失敗不能讓整夜掃描的收尾炸掉。

---

## 4. 報告內容規格

### 4.1 單站報告（樣板：`private/兵役局連結稽核報告.html`）

視覺沿用該樣板的 CSS（讀該檔抽出 `<style>` 區塊到模板常數；配色、卡片、表格、pill 樣式照舊）。
原樣板是「局處版（多站）」，縮放為單站版，區塊：

1. **標題**：`{局處} · {站名} 連結稽核報告`；副標：掃描日期（來源目錄時間戳）、資料來源目錄名、產表時間。
2. **一、摘要**：本站頁數、不重複對外連結數、異常筆數（卡片式）＋固定方法說明文案
   （沿用樣板原文：政府專屬域/社群/白名單只驗存活；其餘驗活＋掃關鍵字，命中經地端 AI 二次研判）。
3. **二、異常連結明細**：欄位 = 風險｜問題連結｜狀態｜說明｜出現頁（`all_locations` 逐行列 `<li>`）。
   依風險排序（🔴→🟠→❓→⚪→DEAD→BROKEN→REDIRECTED→WARN，沿用 `RISK_ORDER` 精神）。
4. **三、已排除誤報（附錄）**：AI 判 C 的連結＋`ai_reason`。一筆都沒有時整區不顯示。

### 4.2 全市總報告（樣板：`private/全市連結稽核總報告.html`，受眾＝資訊局）

1. **一、確認遭入侵/搶註**（置頂）：全市 🔴A 全列＋🟠B 全列（站、局處、URL、型態 type、證據 evidence、AI 理由）。
2. **二、全市異常統計**：卡片（掃描站數、異常合計、可疑、死連、確認掛馬…）＋資料涵蓋說明
   （彙整了哪些日期範圍的幾個深掃目錄、幾站有資料、幾站尚未輪掃到——輪掃進行中資料不全是常態，要標明）。
3. **三、各局處摘要表**：局處｜站數｜可疑｜死連｜HTTP｜重導｜SSL｜合計｜掛馬，可疑/掛馬多者排前，
   局處名錨點連到第四區。
4. **四、各局處異常明細**：每局處 cap 400 筆（沿用樣板慣例），並附該局處單站報告的**相對路徑連結**。
5. **五、每日稽核近況（近 N 天）**：§2.2 的每日寄信版摘要。
6. **六、已排除誤報（附錄）**：全市 C 判定彙總。

### 4.3 檔名清洗

站名做檔名前把 `\/:*?"<>|` 換成 `_`、截 80 字。局處名同樣清洗後當資料夾名。
**重名後綴 `_2` 只處理「同一輪產生中、兩個不同站清洗後同名」**——用本輪記憶體中的
已用名稱集合判斷，**不是**看磁碟上檔案存不存在；同一站重跑必須覆蓋舊檔（見 §8）。

---

## 5. 驗收清單（全部通過才 commit）

```bash
# 1. 編譯
python -m py_compile engine/report_html.py engine/full_overnight.py

# 2. 對既有資料實跑(唯讀來源,產出進 reports_html/)
python -m engine.report_html --org 兵役局
python -m engine.report_html --city --zip

# 3. 數字對帳(寫個臨時腳本驗,不用進版控):
#    - 某站報告的異常筆數 == 該站最新目錄 all_problems.csv 過濾後筆數(剔除C後)
#    - C 判定筆數 == 附錄筆數;主表 grep 不到任何 C 判定的 URL
#    - 全市報告「確認掛馬」筆數 == 各目錄 CONFIRMED_hijacks.csv 最新彙整(URL去重)筆數
# 4. 產出位置全部在 private/reports_html/ 下;git status 不得出現任何產出檔
# 5. 來源目錄的 CSV/JSON 檔 mtime 不變(確認真的唯讀)
# 6. --no-report 旗標存在;full_overnight 收尾掛接處有 try/except
# 7. zip 檔可開、內容=該局處資料夾全部 html
```

驗收 3 的對帳結果（各數字）要寫進回報。

## 6. 文件同步

- `docs/ARCHITECTURE.md`：§3.1 加 `report_html.py` 一列；§7 指令速查加報告產生指令。
- `README.md`：engine 說明行補「報告產生」；「一鍵/常用執行」加一行。
- 本檔不用改。

## 7. 收尾

1. `git add`（確認 `git ls-files private/` 為空）。
2. commit 訊息建議：`feat: report_html 報告產生器(單站/全市,吃AI複查降級,誤報進附錄)`＋ Co-Authored-By 慣例行。
3. **不要 push**——請使用者手動推。
4. 回報：產出了哪些檔、驗收各項結果（含對帳數字）。

---

## 8. 第二輪修正案（首輪交付驗收後發現，2026-07-09）

> 首輪已交付並 commit（`f4d64a3`），獨立驗收發現 **1 個 bug**，本節是修正規格。
> 只改這一件事，其他程式碼不動。

### 8.1 Bug：同站重跑不覆蓋，報告無限疊 `_2`/`_3`

**現象**（驗收實測）：對同一站第二次執行產生器，`reports_html/兵役局/` 出現
`臺北市政府兵役局中文網站.html` 與內容相同的 `..._2.html`，zip 也把重複檔一起打包。

**根因**：`engine/report_html.py` 約 455-463 行的重名偵測用 `os.path.exists(fpath)` 判斷——
磁碟上有**上一輪的舊檔**就誤判為「重名」而改寫到 `_2`。每重跑一次疊一份，
永遠不覆蓋，違反核心設計「輪掃到哪站重產哪站，資料夾裡永遠是每站最新」。

**修正規格**：
1. 重名判斷改用**本輪執行的記憶體集合**：維護 `used_names = set()`（key=`(org_dir_name, fname)`），
   只有「這一輪已經有別的站用掉這個名字」才後綴 `_2`；集合在每次 CLI 執行/`generate_for_sites` 呼叫開始時重置。
2. 名字沒被本輪佔用時**直接寫檔覆蓋**磁碟舊檔（這是重產的正確語意）。
3. 注意 `full_overnight` 收尾掛接呼叫 `generate_for_sites` 時同樣要走這套邏輯。

**連帶清理**：修完後把 `private/reports_html/` 下現有的 `*_2.html`、`*_3.html`…
重複檔刪掉（先確認與本體同名檔並存才刪），並把受影響的 `{局處}.zip` 重壓。

### 8.2 驗收（全部通過才 commit）

```bash
python -m py_compile engine/report_html.py
# 連跑兩次,第二次必須覆蓋而非疊檔:
python -m engine.report_html --org 兵役局
python -m engine.report_html --org 兵役局
ls private/reports_html/兵役局/        # 不得出現任何 _2 檔;檔案數 == 該局處站數
# 重名真陣列仍要工作:寫臨時測試對 _sanitize 後同名的兩個「不同站」驗證會產 _2(可用假資料單元測)
python -m engine.report_html --city --zip
python -c "import zipfile;print(zipfile.ZipFile('private/reports_html/兵役局.zip').namelist())"  # 無 _2
```

commit 訊息建議：`fix: report_html 同站重產改為覆蓋,重名後綴只在同輪內判斷`。不要 push。

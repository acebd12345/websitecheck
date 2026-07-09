# 技術債整併交辦規格（給 terminal Claude 執行）

> 本文件是完整的實作規格，由前期討論定案，**使用者已審閱核准，照做即可，不需再向使用者確認方案**。
> 系統全貌見 [ARCHITECTURE.md](ARCHITECTURE.md)、操作地雷見 [GEMINI_HANDOFF.md](GEMINI_HANDOFF.md) §A。
> 撰寫日：2026-07-09。

---

## 0. 目標與範圍

消除三處「同一件事維護兩份」的漂移源，並把詞庫升級為 Google Sheet 可調：

| # | 項目 | 現況問題 |
|---|---|---|
| 1 | 關鍵字清單 | `daily/audit_links.py` 的 `SUSPICIOUS_KEYWORDS` 與 `engine/full_overnight.py` 的 `BAD_KW` 各自維護、內容重疊但不同步 |
| 2 | SUSPICIOUS 複查邏輯 | `engine/verify_suspicious.py` 與 `full_overnight.ai_verify` 重複，且 verify_suspicious **已漂移出兩個舊 bug**（見 §4） |
| 3 | 分頁參數 | `PAGINATION_PARAMS` 在 `audit_links`（16個）與 `engine/crawl.py`（8個，缺月曆參數）各一份 → engine 爬蟲會掉月曆無限頁陷阱 |

**設計核心**：新增根目錄模組 `scan_settings.py` 作為單一來源——
`Google Sheet「掃描設定」分頁（承辦可調）→ private/scan_settings_cache.json（本機快取）→ 內建 DEFAULTS`。

**明確不做**（已評估過，勿順手擴大範圍）：
- 三個 BFS 爬蟲（audit_links.crawl_internal / engine.crawl / monthly.deep_check）**不合併**，目的不同。
- HTML→text 三條路徑（webcheck_ai.html_to_text / audit_links 的 BS4 get_text / full_overnight.visible_text）**不統一**，語意不同。
- `monthly/webcheck_ai.py:114` 的 `</a>` regex **沒有 bug**（曾被工具顯示假象誤報為 `<\a>`，已用位元組層級確認正確），不要動。
- 停更盤點、雲端化其餘步驟、失效站資料修正——都不在本次範圍。

---

## 1. 鐵律（違反會出事）

1. **git push 只能由使用者手動做**。你只 `git add`/`commit`；push 會卡在 GCM 登入視窗逾時。
2. **寫 Google Sheet 一律非破壞**：只新增、不覆蓋、不刪除。寫前先檢查目標是否已存在，存在就跳過。
3. **repo 是 public**：機敏（config.json、金鑰、內部 AI 主機名）只能在 `private/`（已整包 gitignore）。commit 前掃：
   `git ls-files private/`（須空）。
4. 本專案是正式的政府營運系統。**詞庫絕不允許為空**——詞庫空 = 整夜掃描零候選、默默漏光，比程式掛掉更糟。所有 fallback 設計都是為了這件事。
5. 檔案編碼一律 UTF-8，保留 `# -*- coding: utf-8 -*-` 檔頭。

---

## 2. 工作一：新增 `scan_settings.py`（根目錄）

完整檔案內容如下，直接建立（此程式碼已在前期驗證過設計）：

```python
# -*- coding: utf-8 -*-
"""掃描設定單一來源:Google Sheet「掃描設定」分頁 → 本機快取 → 內建預設。

詞庫(賭博/色情/停放頁關鍵字)與分頁參數以前散在 audit_links/full_overnight/crawl
各自維護一份,改一邊另一邊不會同步(漂移)。改為:
  Sheet「掃描設定」分頁(承辦可調) → private/scan_settings_cache.json(快取) → DEFAULTS(內建)

用法:
  - 主流程(full_overnight / batch_audit)啟動時呼叫一次 refresh():
    讀 Sheet 成功 → 更新快取;失敗 → 沿用既有快取或內建預設(印警示,不中斷)。
  - 其他任何地方(含 ProcessPoolExecutor 子行程)只呼叫 get(key):
    讀快取檔,絕不打 Sheet API(466 個子行程各打一次會撞配額)。
  - get() 保證回非空清單:Sheet 該列缺漏/清空時退回內建預設——
    詞庫變空 = 整夜掃描零候選、默默漏光,比程式掛掉更糟。

比對「語意」(整字邊界、善意詞剔除、裸搜計數)留在使用端程式,Sheet 只管「詞」。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

SHEET_WS = "掃描設定"
CACHE_PATH = os.path.join(config.PRIVATE_DIR, "scan_settings_cache.json")

DEFAULTS = {
    # 第一關圈候選的內容詞(audit_links 整字邊界比對+善意詞剔除;也供第三關定性)
    "suspicious_keywords": [
        "娛樂城", "百家樂", "博弈", "博彩", "賭場", "老虎機", "捕魚機", "六合彩",
        "casino", "baccarat", "slot", "betting", "poker", "jackpot",
        "色情", "成人影片", "成人視訊", "情色", "約砲", "av女優", "無碼",
        "porn", "hentai", "xvideo", "live sex", "escort",
        "виагра", "viagra", "cialis",
    ],
    # 停放/出售頁詞(只給第一關;第三關的停放判定走 AI verdict B,不吃這組)
    "parked_keywords": [
        "domain is for sale", "buy this domain", "此網域可供出售", "域名出售",
        "parked domain", "sedoparking", "godaddy.com/domainsearch",
    ],
    # 第三關定性補充詞:短詞/品牌片段(dewa77、situs judi),只適合裸搜,
    # 整字邊界比對反而抓不到(dewa 後面接 77 會被邊界規則擋掉),故不併入 suspicious
    "characterize_extra_keywords": ["sex", "dewa", "judi", "gacor", "situs"],
    # 比對前先剔除的善意詞(白色情人節撞「色情」、註冊商把 .casino 當商品名賣)
    "benign_phrases": ["白色情人節", ".casino", ".poker", ".bet", ".slot", ".xxx", ".sexy", ".porn"],
    # 分頁/月曆參數:URL 帶這些參數視為分頁,不再往下挖(月曆無限頁陷阱)
    "pagination_params": [
        "page", "pagesize", "offset", "limit", "start", "count", "p", "pn",
        "pageindex", "pageno", "cid", "date", "month", "year", "yy", "mm",
    ],
}

_cache_mem = None  # 每行程讀一次快取檔


def _parse_rows(rows):
    """從分頁的原始列(參數|值|型別|用途)撈出本模組認得的參數;值=逗號分隔。"""
    out = {}
    for row in rows:
        if len(row) >= 2 and row[0].strip() in DEFAULTS:
            vals = [v.strip() for v in str(row[1]).split(",") if v.strip()]
            if vals:
                out[row[0].strip()] = vals
    return out


def refresh():
    """讀 Sheet「掃描設定」→ 寫本機快取。回 (成功?, 訊息)。失敗不丟例外、不中斷主流程。"""
    global _cache_mem
    try:
        import gspread
        gc = gspread.service_account(filename=config.GA_KEY_FILE)
        ws = gc.open_by_key(config.MASTER_SHEET_ID).worksheet(SHEET_WS)
        found = _parse_rows(ws.get_all_values())
        data = {"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "values": found}
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        _cache_mem = found
        missing = [k for k in DEFAULTS if k not in found]
        msg = f"掃描設定:Sheet 讀到 {len(found)} 組參數"
        if missing:
            msg += f",缺 {','.join(missing)}(用內建預設)"
        return True, msg
    except Exception as e:
        src = "既有快取" if os.path.exists(CACHE_PATH) else "內建預設"
        return False, f"掃描設定:讀 Sheet 失敗({type(e).__name__}),沿用{src}"


def _load_cache():
    global _cache_mem
    if _cache_mem is None:
        try:
            _cache_mem = json.load(open(CACHE_PATH, encoding="utf-8")).get("values", {})
        except Exception:
            _cache_mem = {}
    return _cache_mem


def get(key):
    """取設定值(list)。快取 → 內建預設,保證非空。"""
    v = _load_cache().get(key)
    return list(v) if v else list(DEFAULTS[key])
```

**不變式（驗收會查）**：`DEFAULTS["suspicious_keywords"] + DEFAULTS["parked_keywords"]` 必須與改動前
`audit_links.SUSPICIOUS_KEYWORDS` 的 36 個詞**完全一致（含順序）**——第一關行為零變更。

---

## 3. 工作二：`daily/audit_links.py` 改吃單一詞庫

三處修改：

**(a) import 區**（現有 `import re` 開頭那段）加：

```python
import os   # 若尚未 import
# ...既有 import 之後:
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scan_settings  # 詞庫/分頁參數單一來源(Sheet掃描設定→快取→內建預設)
```

**(b)** 刪除 `SUSPICIOUS_KEYWORDS = [...]` 與 `BENIGN_PHRASES = [...]` 的字面清單，改為：

```python
# 可疑內容關鍵字(賭博、色情 + 停放頁):單一來源見 scan_settings(Sheet 可調)
SUSPICIOUS_KEYWORDS = (scan_settings.get("suspicious_keywords")
                       + scan_settings.get("parked_keywords"))

# 比對前先剔除的善意詞(避免「白色情人節」誤撞「色情」這類子字串誤判)
# 帶點的 TLD 商品名:域名註冊商首頁把 .casino/.poker/.bet 當商品賣,非賭博內容
BENIGN_PHRASES = scan_settings.get("benign_phrases")
```

**(c)** `PAGINATION_PARAMS` 字面 set 改為：

```python
# 分頁/清單參數:含這些的內部 URL 視為分頁,不再往下挖(避免月曆/分頁製造無限內部頁)
PAGINATION_PARAMS = {p.lower() for p in scan_settings.get("pagination_params")}
```

其餘（`_kw_pattern`、`KEYWORD_PATTERNS`、`BENIGN_PHRASES` 的使用處）**全部不動**——比對語意留在程式。

---

## 4. 工作三：`engine/full_overnight.py`——BAD_KW 單一來源 + `--verify` 旗標

### 4.1 背景：verify_suspicious.py 的兩個已漂移 bug（這是本工作的動機）

- `engine/verify_suspicious.py:63`：抓取失敗**直接判 B（停放/搶註）**。這正是專案已修過的誤報根因——
  `full_overnight.ai_verify` 已改成「重試 3 次、仍失敗判 `?` 待人工」（dac.tw/taitraesource 教訓，正當媒體擋爬別誤標）。
- `verify_suspicious.py:50`：預設輸入路徑 glob `linkaudit_all_*`，但產生它的 `link_audit_all.py` 已刪除，
  新報告都叫 `full_overnight_*` → 不帶參數會炸掉或默默撿舊資料。

**處置**：`git rm engine/verify_suspicious.py`，功能由 `full_overnight --verify` 取代（決策已定，勿保留該檔）。

### 4.2 修改內容

**(a) import 區**（`import config` 之後）加 `import scan_settings`。

**(b)** `BAD_KW = [...]` 字面清單改為：

```python
# 定性用詞庫(裸搜計數,只跑在 AI 已判 A/B 的頁上):單一來源見 scan_settings。
# 藥物詞(viagra/cialis)只適合整字比對,裸搜會撞 specialist 類字 → 排除;
# 品牌片段(dewa/judi/gacor)反之只適合裸搜 → 由 characterize_extra 補入。
_AMBIG_SUBSTR = {"viagra", "cialis", "виагра"}

def _build_bad_kw():
    return ([k for k in scan_settings.get("suspicious_keywords") if k not in _AMBIG_SUBSTR]
            + scan_settings.get("characterize_extra_keywords"))

BAD_KW = _build_bad_kw()
```

> ⚠ 這是**本次唯一的行為變更**（已核准）：BAD_KW 從 19 詞變 31 詞（新增 捕魚機/六合彩/baccarat/jackpot/
> 色情/成人視訊/情色/約砲/無碼/hentai/xvideo/live sex）。它只影響「AI 已判 A/B 的頁」的型態定性，
> 不影響第一關圈候選。viagra/cialis 刻意排除（裸搜會撞 specialist）。

**(c)** `characterize()` 內三處裸搜加 `re.escape`（詞庫上 Sheet 後承辦可能填帶點的詞）：

```python
            if re.search(re.escape(kw), h, re.I):
    vis_hits = [kw for kw in BAD_KW if len(re.findall(re.escape(kw), vis, re.I)) >= 2]
    title_bad = any(re.search(re.escape(kw), title, re.I) for kw in BAD_KW + ["gaming"])
```
（原本 `BAD_KW + ["dewa","gaming"]` 的 `"dewa"` 可拿掉——已在 characterize_extra 裡。）

**(d) `main()` 開頭**（argparse 之後）：

```python
    ap.add_argument("--verify", default="", help="複查:不重爬,對既有報告目錄的 all_problems.csv 重跑階段2-4(取代舊 verify_suspicious)")
    args = ap.parse_args()
    if args.verify and args.resume:
        sys.exit("--verify 與 --resume 不可同時使用")

    # 詞庫/分頁參數:Sheet「掃描設定」→本機快取;失敗沿用快取/內建預設,不中斷
    global BAD_KW
    _, ss_msg = scan_settings.refresh()
    BAD_KW = _build_bad_kw()   # refresh 後重建(模組載入時建的可能是舊快取)
    print(ss_msg, flush=True)
```

> 為何要 refresh 後重建：`BAD_KW` 在模組載入時就建好了，同行程事後 refresh 不會回頭改它。
> 階段1 的子行程沒這問題（spawn 全新行程、重新 import、讀到的是 refresh 後的快取）。

**(e) outdir 選擇**：`--verify` 與 `--resume` 同格式（絕對路徑或 `reports/` 下目錄名）：

```python
    if args.verify:
        outdir = args.verify if os.path.isabs(args.verify) else os.path.join(OUT_DIR, "reports", args.verify)
        stamp = os.path.basename(outdir).replace("full_overnight_", "")
    elif args.resume:
        ...(原邏輯)
```

**(f) verify 模式前置**（`combined`/`progress` 路徑算出來之後）：

```python
    if args.verify:
        if not os.path.exists(combined):
            sys.exit(f"--verify 找不到 {combined}")
        t0 = time.time()
        prog = json.load(open(progress, encoding="utf-8")) if os.path.exists(progress) else []
        skipped = [p for p in prog if p.get("status") not in ("ok",)
                   and not str(p.get("status", "")).startswith("fail")]
        print(f"===== 複查模式 {stamp}:跳過階段1,對既有 all_problems.csv 重跑階段2-4 =====\n", flush=True)
```

**(g)** 把整個階段1區塊（從 `# 續跑:載入已完成站的 URL` 到 `print(f"\n階段1 完成 ...")` 為止，
含讀 CSV 清單、ProcessPoolExecutor、頁數寫回 Sheet）包進 `if not args.verify:`（整段縮排 +4）。
階段2/3/4 **不動**——verify 模式下它們自然讀既有 `combined`，並覆寫該目錄的
`suspicious_verified.*` 與 `CONFIRMED_hijacks.csv`（這正是「重新複查」的語意）。

**(h)** 階段4 的 `summary` dict 加一鍵標記模式：`"mode": "verify" if args.verify else "full",`
（複查會覆寫該目錄 summary.json，標記讓人看得出這份是複查產物）。

檢查點：verify 分支用到的 `t0`/`prog`/`skipped` 已在 (f) 給值；stage 2-4 引用的其他變數不得殘留在被包進 (g) 的區塊外。

---

## 5. 工作四：`daily/batch_audit.py` 啟動時刷新詞庫

在 import 區、**`from audit_links import ...` 之前**插入（順序是關鍵）：

```python
# 詞庫快取須在 import audit_links「之前」更新:audit_links 於載入時就編好關鍵字樣板,
# 同行程事後 refresh 不會回頭改它。Sheet 讀失敗會沿用快取/內建預設,不中斷排程。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scan_settings
print(scan_settings.refresh()[1], flush=True)

from audit_links import audit_site, norm_host, CSV_COLS, RISK_ORDER
```

（batch_audit 在 Linux 主機 cron 跑，主機備有同一把 service account 金鑰；讀不到 Sheet 時 fallback 保證不中斷排程。）

---

## 6. 工作五：`engine/crawl.py` 分頁參數統一

**(a)** import 區加 `import scan_settings`（該檔已有 `_ROOT` sys.path 處理，直接 import 即可）。

**(b)** 字面 `PAGINATION_PARAMS = {8個}` 改為：

```python
# 單一來源(scan_settings):16 參數完整版,含月曆類(date/month/year),
# 之前這裡只有 8 個、缺月曆參數 → 健康剖面爬蟲會掉進月曆無限頁陷阱
PAGINATION_PARAMS = {p.lower() for p in scan_settings.get("pagination_params")}
```

> 行為說明（可接受，已核准）：engine 爬蟲會多跳過月曆/分頁 URL，掃出頁數可能變少；
> 主設定表「頁數」欄只在變大時回寫，不會縮水。
> `scan.py`/`run_all.py` 不用加 refresh——它們吃 full_overnight/batch_audit 刷新過的快取即可。

---

## 7. 工作六：Google Sheet「掃描設定」分頁新增 5 列

**以下列值使用者已核准，直接寫入**。規則：
- 用 gspread（金鑰 `config.GA_KEY_FILE`、試算表 ID `config.MASTER_SHEET_ID`）。
- 先讀分頁現有第 1 欄，**若參數名已存在則跳過該列不寫**（防重複執行疊加）。
- 用 append 方式加在「全域參數」區塊表格之後（B 區「每站可覆寫欄位」之前若做不到精準插入，直接 append 到分頁最尾也可以——`_parse_rows` 是掃全部列找參數名，位置不影響功能，但盡量插在 A 區尾維持可讀性）。
- 格式與既有列一致：`參數 | 值 | 型別 | 用途`。

| 參數 | 值（逗號分隔，一格塞完） | 型別 | 用途 |
|---|---|---|---|
| `suspicious_keywords` | `娛樂城,百家樂,博弈,博彩,賭場,老虎機,捕魚機,六合彩,casino,baccarat,slot,betting,poker,jackpot,色情,成人影片,成人視訊,情色,約砲,av女優,無碼,porn,hentai,xvideo,live sex,escort,виагра,viagra,cialis` | csv | 賭博/色情內容詞;第一關整字邊界比對圈候選+第三關定性 |
| `parked_keywords` | `domain is for sale,buy this domain,此網域可供出售,域名出售,parked domain,sedoparking,godaddy.com/domainsearch` | csv | 停放/出售頁詞;只給第一關 |
| `characterize_extra_keywords` | `sex,dewa,judi,gacor,situs` | csv | 第三關定性補充(品牌片段只適合裸搜) |
| `benign_phrases` | `白色情人節,.casino,.poker,.bet,.slot,.xxx,.sexy,.porn` | csv | 比對前剔除的善意詞(防子字串誤判) |
| `pagination_params` | `page,pagesize,offset,limit,start,count,p,pn,pageindex,pageno,cid,date,month,year,yy,mm` | csv | URL帶這些參數視為分頁不往下挖(月曆陷阱) |

寫完後跑一次 `python -c "import scan_settings; print(scan_settings.refresh())"` 確認回
`(True, '掃描設定:Sheet 讀到 5 組參數')`。

---

## 8. 驗收清單（全部通過才 commit）

```bash
# 1. 編譯
python -m py_compile scan_settings.py daily/audit_links.py daily/batch_audit.py engine/full_overnight.py engine/crawl.py

# 2. 第一關詞庫零變更(不變式):合成清單 == 舊 36 詞
python - <<'EOF'
import sys; sys.path[:0] = [".", "daily"]
import scan_settings, importlib
OLD = ["娛樂城","百家樂","博弈","博彩","賭場","老虎機","捕魚機","六合彩",
 "casino","baccarat","slot","betting","poker","jackpot",
 "色情","成人影片","成人視訊","情色","約砲","av女優","無碼",
 "porn","hentai","xvideo","live sex","escort","виагра","viagra","cialis",
 "domain is for sale","buy this domain","此網域可供出售","域名出售",
 "parked domain","sedoparking","godaddy.com/domainsearch"]
import audit_links
assert audit_links.SUSPICIOUS_KEYWORDS == OLD, "第一關詞庫變了!"
assert len(audit_links.PAGINATION_PARAMS) == 16
assert len(audit_links.KEYWORD_PATTERNS) == 36
print("OK: stage-1 unchanged")
EOF

# 3. BAD_KW 涵蓋舊 19 詞(超集,且無 viagra/cialis)
python - <<'EOF'
import sys; sys.path[:0] = [".", "daily", "monthly"]
from engine import full_overnight as fo
OLD = {"casino","poker","slot","betting","娛樂城","百家樂","博弈","賭場","escort","porn",
 "sex","dewa","judi","gacor","situs","博彩","老虎機","av女優","成人影片"}
assert OLD <= set(fo.BAD_KW) and not ({"viagra","cialis"} & set(fo.BAD_KW))
print("OK: BAD_KW superset, ambig excluded")
EOF

# 4. fallback:暫時改名快取檔後 get() 仍回內建預設(非空),測完改回來
# 5. crawl 分頁參數 16 個
python -c "import sys; sys.path.insert(0,'.'); from engine import crawl; assert len(crawl.PAGINATION_PARAMS)==16; print('OK')"

# 6. --verify 冒煙:挑 private/reports/ 下一個小的 full_overnight_* 目錄「整個複製」到暫存處,
#    對副本跑 python -m engine.full_overnight --verify <副本絕對路徑>
#    (會呼叫地端 AI;若端點不通,ai_verify 會判 ? 待人工,流程仍應正常跑完產出 summary.json mode=verify)
#    ⚠ 不要對原目錄跑,避免覆寫既有複查產出。

# 7. 互斥檢查:--verify 加 --resume 應直接退出
python -m engine.full_overnight --verify x --resume y ; echo "exit=$?"
```

---

## 9. 文件同步更新

- `docs/ARCHITECTURE.md`：§3.1 表刪 `verify_suspicious.py` 列、加 `scan_settings.py`(根目錄)說明；
  §6 技術債把「SUSPICIOUS 第二關有兩份」「關鍵字清單有兩份」「PAGINATION_PARAMS 兩份」標為已解（比照既有 ~~刪除線~~ 慣例）；
  §7 指令速查加 `python -m engine.full_overnight --verify <報告目錄>`。
- `docs/cloud/scan-settings-tab.md`：區塊 A 表格補上 §7 的 5 個新參數。
- `README.md`：資料夾結構加 `scan_settings.py` 一行。
- `docs/GEMINI_HANDOFF.md` §D 待辦 5「消除重複」標記已完成（註明由本規格執行）。

## 10. 收尾

1. `git add` 相關檔案（確認 `git ls-files private/` 為空、`git status` 無 private 檔）。
2. commit 訊息建議：`refactor: 詞庫/分頁參數單一來源化(Sheet掃描設定) + full_overnight --verify 取代 verify_suspicious`，
   結尾附 `Co-Authored-By:` 慣例行。
3. **不要 push**——請使用者手動 `git push origin main`。
4. 向使用者回報：改了哪些檔、Sheet 寫了哪幾列、驗收清單各項結果。

# 「掃描設定」分頁 — 內容設計（提案）

> 目的：上雲後**只讀 Google Sheet 就能拿到完整掃描設定**。此分頁為**新增、非破壞性**，不動既有分頁。
> ⚠ 機敏值（AI 金鑰、Gmail app_password、service account、master_sheet_id、AI 端點）**不放這裡**，走環境變數 / Secret Manager，見 SECURITY_CLOUD.md。

## 區塊 A：全域參數（參數 / 值 / 型別 / 用途）

| 參數 | 建議值 | 型別 | 用途 |
|---|---|---|---|
| `max_pages` | 8000 | int | 每站首掃頁數上限(新站用);Sheet頁數欄有值的站用 該值+page_buffer |
| `page_ceil` | 9000 | int | 加碼爬頁硬上限,撞到就停(引擎 CEIL) |
| `page_buffer` | 100 | int | 已知站首掃 = Sheet頁數 + 此值(page_budget.BUFFER) |
| `escalate_steps` | 1000,3000,6000,9000 | csv-int | 撞牆加碼序列 |
| `workers` | 6 | int | 站層級平行行程數 |
| `render_cap` | 60 | int | playwright 站每站最多渲染幾頁空殼(_RENDER_CAP) |
| `daily_page_budget` | 5000 | int | 分批排程:每日頁數預算 |
| `daily_site_cap` | 40 | int | 分批排程:每日站數上限 |
| `giant_threshold` | 5000 | int | 單站>=此值視為巨站,獨立日/獨立時段 |
| `ai_fetch_retry` | 3 | int | stage2/3 複查抓取失敗重試次數 |
| `ai_fetch_retry_sleep` | 4 | int | 每次重試間隔秒 |
| `schedule_groups` | 5 | int | daily 寄信版分幾組(按星期輪掃) |
| `log_keep_days` | 3 | int | log 保留天數 |
| `content_whitelist` | porkbun.com, cndns.com, comlaude.com, domaine.fr, gname.c… | csv-host | 白名單:跳過關鍵字檢查,仍驗存活 |
| `skip_hosts` | accessibility.moda.gov.tw, pcc.gov.tw, web.pcc.gov.tw, cr… | csv-host | 免檢名單:完全略過(有防爬蟲、人工確認正常) |
| `skip_methods` | manual,疑似失效 | csv | 這些抓取方式的站不掃描 |
| `skip_hosts_hard` | 3d.taipei | csv-host | 硬跳過的 host(3D應用) |
| `suspicious_keywords` | 娛樂城,百家樂,博弈,博彩,賭場,老虎機,捕魚機,六合彩,casino,baccarat,slot,betting,poker,jackpot,色情,成人影片,成人視訊,情色,約砲,av女優,無碼,porn,hentai,xvideo,live sex,escort,виагра,viagra,cialis | csv | 賭博/色情內容詞;第一關整字邊界比對圈候選+第三關定性 |
| `parked_keywords` | domain is for sale,buy this domain,此網域可供出售,域名出售,parked domain,sedoparking,godaddy.com/domainsearch | csv | 停放/出售頁詞;只給第一關 |
| `characterize_extra_keywords` | sex,dewa,judi,gacor,situs | csv | 第三關定性補充(品牌片段只適合裸搜) |
| `benign_phrases` | 白色情人節,.casino,.poker,.bet,.slot,.xxx,.sexy,.porn | csv | 比對前剔除的善意詞(防子字串誤判) |
| `pagination_params` | page,pagesize,offset,limit,start,count,p,pn,pageindex,pageno,cid,date,month,year,yy,mm | csv | URL帶這些參數視為分頁不往下挖(月曆陷阱) |

（`content_whitelist` / `skip_hosts` 完整值見 config.json，上雲時原樣搬進此格。）

## 區塊 B：每站可覆寫欄位（對應 TCGweb466站清單 既有欄）

雲端逐站掃描時，從 `TCGweb466站清單` 讀以下欄；此處只是**欄位語意對照表**，不重複存資料。

| 欄位 | 型別 | 語意 |
|---|---|---|
| `網址` | string | 起爬 URL(唯一鍵,寫回頁數也用它對鍵) |
| `內容抓取方式` | enum | code/ai=靜態爬;playwright=深掃時開渲染;manual/疑似失效=跳過 |
| `depth` | int(可空) | (保留)限制深掃層數;目前空=不限 |
| `pagination` | string(可空) | (保留)額外分頁參數跳過;空=用內建 PAGINATION_PARAMS |
| `頁數` | int | 每站上次實際頁數;引擎掃完自動回填,決定下次首掃上限 |
| `頁數更新日` | date | 頁數最後更新日 |
| `web_check14站` | 是/空 | 是否納入每月合規 14 站 AI 判讀集 |
| `局處` | string | 分批/報表分組用 |

## 寫入方式
- 新增分頁名稱：`掃描設定`
- A 區從第 1 列起（表頭 `參數/值/型別/用途`），B 區空一列後接。
- 全部為新增，**不覆蓋、不刪除**任何既有分頁或欄位。
# -*- coding: utf-8 -*-
"""
從 Google Sheet「主設定表」同步出兩套工具的設定檔：
  - D:\\websitecheck\\sites.json        (monthly_check / ga_traffic / node_check 用)
  - link_audit\\domains.txt             (link_audit 對外連結稽核用)

每次跑檢核前自動執行(由 每月檢核.bat 呼叫)，所以以後只要改 Google Sheet 主設定表，
兩邊設定自動更新，不必手動編輯 json / txt。

主設定表欄位：
  工作表名稱 | 網站名稱 | 網址 | 填表人 | 分機 | Email |
  GA資源ID | GA指標 | AI判讀題目 | 稽核收件人 | 副本

用法: python sync_config.py
"""
import json
import os

import gspread
import config

BASE_DIR = config.BASE_DIR
KEY = config.GA_KEY_FILE
SHEET_ID = config.MASTER_SHEET_ID
MASTER_WS = config.MASTER_WORKSHEET
LINK_AUDIT_DOMAINS = os.path.join(config.LINK_AUDIT_DIR, "domains.txt") if config.LINK_AUDIT_DIR else ""


def load_master():
    gc = gspread.service_account(filename=KEY)
    ws = gc.open_by_key(SHEET_ID).worksheet(MASTER_WS)
    return ws.get_all_records()  # list of dict, key=表頭


def to_sites_json(rows):
    sites = []
    for r in rows:
        urls = [u.strip() for u in str(r.get("網址", "")).split(";") if u.strip()]
        if not urls:
            continue
        site = {"sheet": r["工作表名稱"], "name": r["網站名稱"], "urls": urls,
                "person": str(r.get("填表人", "")).strip(),
                "ext": str(r.get("分機", "")).strip(),
                "email": str(r.get("Email", "")).strip(),
                "method": str(r.get("內容抓取方式", "")).strip() or "ai"}
        gid = str(r.get("GA資源ID", "")).strip()
        if gid:
            site["ga_property"] = gid
            metric = str(r.get("GA指標", "")).strip() or "screenPageViews"
            site["ga_metric"] = metric
        ai = str(r.get("AI判讀題目", "")).strip()
        if ai:
            # 支援「網址|題目」覆寫，否則用主網址
            if "|" in ai:
                u, q = ai.split("|", 1)
                site["ai_checks"] = [{"url": u.strip(), "question": q.strip()}]
            else:
                site["ai_checks"] = [{"url": urls[0], "question": ai}]
        else:
            site["ai_checks"] = []
        sites.append(site)
    return {"sites": sites}


def to_domains_txt(rows):
    lines = ["# 由 sync_config.py 從 Google Sheet 主設定表自動產生，請勿手動編輯",
             "# 格式: 網站名稱,網址,收件人,副本"]
    for r in rows:
        urls = [u.strip() for u in str(r.get("網址", "")).split(";") if u.strip()]
        to = str(r.get("稽核收件人", "")).strip()
        cc = str(r.get("副本", "")).strip()
        if not urls or not to:
            continue
        name = str(r["網站名稱"]).replace(",", "，")  # 站名不能含半形逗號
        lines.append(f"{name},{urls[0]},{to},{cc}")
    return "\n".join(lines) + "\n"


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    rows = load_master()
    print(f"主設定表讀取 {len(rows)} 站")

    sj = to_sites_json(rows)
    with open(config.SITES_JSON, "w", encoding="utf-8") as f:
        json.dump(sj, f, ensure_ascii=False, indent=2)
    ga_n = sum(1 for s in sj["sites"] if s.get("ga_property"))
    print(f"  → sites.json 已更新（{len(sj['sites'])} 站，{ga_n} 站有 GA）")

    if os.path.isdir(os.path.dirname(LINK_AUDIT_DOMAINS)):
        with open(LINK_AUDIT_DOMAINS, "w", encoding="utf-8") as f:
            f.write(to_domains_txt(rows))
        print(f"  → domains.txt 已更新")
    else:
        print(f"  (略過 domains.txt：找不到 link_audit 資料夾)")


if __name__ == "__main__":
    main()

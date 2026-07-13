# -*- coding: utf-8 -*-
"""
GA4 流量撈取工具 (檢核表「網站流量數」= 瀏覽網頁數 = GA4 screenPageViews)

用法:
  python ga_traffic.py <PropertyID>            撈該資源上個月的瀏覽網頁數
  python ga_traffic.py <PropertyID> 11505      撈指定民國年月
  python ga_traffic.py --list                  列出服務帳戶可存取的所有GA資源(需Admin API)

前置設定 (一次性):
  1. GCP 專案啟用「Google Analytics Data API」與「Admin API」
  2. 到各網站 GA4 後台: 管理 → 資源存取權管理 → 加入服務帳戶為檢視者
     (服務帳戶 email 見金鑰檔 client_email 欄位)
  3. 府內網站表對應網站填上 GA資源ID 與 GA指標
"""
import calendar
import datetime
import json
import sys

from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

import config
BASE_DIR = config.BASE_DIR
KEY_FILE = config.GA_KEY_FILE
SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]


def _session():
    creds = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
    return AuthorizedSession(creds)


def roc_month_range(roc_ym=None):
    """民國年月(如'11505') → ('2026-05-01','2026-05-31'); 不給則取上個月"""
    if roc_ym:
        y, m = int(roc_ym[:-2]) + 1911, int(roc_ym[-2:])
    else:
        t = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
        y, m = t.year, t.month
    last = calendar.monthrange(y, m)[1]
    return f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last:02d}"


def fetch_pageviews(property_id, start, end, metric="screenPageViews"):
    """回傳 (流量數int, 錯誤訊息str或None)。metric 預設瀏覽網頁數，
    可指定 activeUsers(活躍使用者)、sessions(工作階段)等 GA4 指標。"""
    s = _session()
    r = s.post(
        f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport",
        json={"dateRanges": [{"startDate": start, "endDate": end}],
              "metrics": [{"name": metric}]})
    data = r.json()
    if "error" in data:
        return None, f"{data['error'].get('status')}: {data['error'].get('message','')[:200]}"
    rows = data.get("rows", [])
    if not rows:
        return 0, None
    return int(float(rows[0]["metricValues"][0]["value"])), None


def list_properties():
    s = _session()
    r = s.get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries")
    data = r.json()
    if "error" in data:
        print("錯誤:", data["error"].get("message", "")[:200])
        return
    for acc in data.get("accountSummaries", []):
        print("帳戶:", acc.get("displayName"))
        for p in acc.get("propertySummaries", []):
            pid = p.get("property", "").replace("properties/", "")
            print(f"   {p.get('displayName')}  →  ga_property: \"{pid}\"")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--list":
        list_properties()
        return
    pid = args[0]
    start, end = roc_month_range(args[1] if len(args) > 1 else None)
    n, err = fetch_pageviews(pid, start, end)
    if err:
        print(f"撈取失敗: {err}")
    else:
        print(f"資源 {pid}  {start} ~ {end}  瀏覽網頁數: {n:,}")


if __name__ == "__main__":
    main()

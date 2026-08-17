# -*- coding: utf-8 -*-
"""月度白名單健檢。

白名單(掃描設定分頁 skip_hosts)平常掃描「完全略過」,等於對它們瞎眼。
本模組每月用 playwright 真瀏覽器逐站重載一次,確認:
  - 仍能載入(沒死),且
  - 未變成賭博/色情(命中不當關鍵字)。
失敗或命中的 → 列入報告示警,提示人工複核是否該移出白名單。
不自動改白名單(避免誤刪),只產報告 + 選擇性寄信通知。

用法(主機每月 cron):
  .venv/bin/python3 -m engine.whitelist_recheck [--mail]
"""
import os, sys, csv, io, re, argparse, datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import config
import scan_settings

# 健檢用的不當關鍵字(裸搜只作初篩;命中僅示警、由人複核,不當判定)
BAD_KW = [
    "娛樂城", "百家樂", "博弈", "賭場", "老虎機", "六合彩", "下注", "投注",
    "casino", "baccarat", "slot", "poker", "jackpot", "togel", "situs", "judi", "gacor",
    "色情", "成人影片", "情色", "外送茶", "外約", "porn", "hentai", "xvideo", "escort",
    "for sale", "buy this domain", "網域出售", "域名出售",
]


def _load(pg, host):
    """回傳 (status, title, hits, err)。https 失敗退 http。"""
    for scheme in ("https://", "http://"):
        try:
            resp = pg.goto(scheme + host + "/", wait_until="domcontentloaded", timeout=25000)
            status = resp.status if resp else 0
            title = (pg.title() or "")[:80]
            body = pg.inner_text("body")[:4000] if pg.query_selector("body") else ""
            low = (title + " " + body + " " + pg.url).lower()
            hits = [k for k in BAD_KW if k.lower() in low]
            return status, title, hits, ""
        except Exception as e:
            last = type(e).__name__
    return 0, "", [], f"載入失敗({last})"


def recheck(do_mail=False):
    scan_settings.refresh()
    hosts = list(dict.fromkeys(h for h in scan_settings.get("skip_hosts") if h.strip()))
    stamp = datetime.date.today().strftime("%Y-%m-%d")
    outdir = os.path.join(config.PRIVATE_DIR, "reports")
    os.makedirs(outdir, exist_ok=True)
    out_csv = os.path.join(outdir, f"whitelist_recheck_{stamp}.csv")

    from playwright.sync_api import sync_playwright
    rows, flagged = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for i, h in enumerate(hosts, 1):
            pg = b.new_page()
            try:
                status, title, hits, err = _load(pg, h)
            finally:
                pg.close()
            state = "OK"
            if err:
                state = "載入失敗"
            elif hits:
                state = "命中不當關鍵字"
            elif status and status >= 400:
                state = f"HTTP {status}"
            row = [h, status, title, "|".join(hits), state, err]
            rows.append(row)
            if state != "OK":
                flagged.append(row)
            if i % 10 == 0:
                print(f"  {i}/{len(hosts)}", flush=True)
        b.close()

    with io.open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["白名單host", "status", "title", "關鍵字命中", "健檢結果", "錯誤"])
        for r in rows:
            w.writerow(r)

    print(f"白名單健檢完成:{len(hosts)} 站,異常 {len(flagged)} 站 → {out_csv}", flush=True)
    for r in flagged:
        print(f"  ⚠ {r[0]} :: {r[4]} {('['+r[3]+']') if r[3] else ''}", flush=True)

    if do_mail and flagged:
        try:
            from engine import mailer
            body = ["<h3>白名單月度健檢:發現異常</h3>",
                    f"<p>{stamp} 重驗 skip_hosts {len(hosts)} 站,以下 {len(flagged)} 站需人工複核"
                    "(可能已死/變賭博色情,考慮移出白名單):</p><ul>"]
            for r in flagged:
                body.append(f"<li><b>{r[0]}</b> — {r[4]} {('['+r[3]+']') if r[3] else ''}</li>")
            body.append("</ul><p>詳見附件。</p>")
            to = config.get("mail_override_to", "")   # 收件人走 config,不硬編信箱(repo 公開)
            if to:
                mailer.send_mail(to, f"[白名單健檢] {stamp} 發現 {len(flagged)} 站異常",
                                 "".join(body), out_csv)
                print(f"已寄健檢通知 → {to}", flush=True)
            else:
                print("[提示] 未設 mail_override_to,略過健檢通知寄信", flush=True)
        except Exception as e:
            print(f"[警告] 健檢寄信失敗({type(e).__name__}: {e})", flush=True)

    return flagged


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="月度白名單健檢")
    ap.add_argument("--mail", action="store_true", help="有異常時寄信通知(預設關)")
    args = ap.parse_args()
    recheck(do_mail=args.mail)


if __name__ == "__main__":
    main()

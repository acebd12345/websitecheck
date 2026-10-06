# -*- coding: utf-8 -*-
"""寄信中繼服務(跑在府內主機,把 websitecheck 的信轉交府內 Mail Relay)。

為什麼要有它:websitecheck 主機在府外,府內 Mail Relay 只收府內主機的 SMTP;
府外 → 府內只開 HTTPS(防火牆說明表3:25 只能連本府 Mail Relay)。所以府外主機用
HTTPS 把信交給這支服務,由它在府內用 SMTP 交給 Mail Relay。

    websitecheck(mailer.py, method=relay) ─HTTPS POST /send→ 本服務 ─SMTP 25→ Mail Relay

只用 Python 標準庫(府內主機連不到外網,不能 pip install)。
所有位址、帳號、token 一律讀環境變數(systemd EnvironmentFile),**本檔不得寫死**——repo 是 public。

環境變數(範本見 mailrelay.env.example):
  RELAY_TOKEN        必填。呼叫端要帶 Authorization: Bearer <token>
  RELAY_FROM         必填。寄件人(固定,呼叫端不能改,避免被拿去冒名寄信)
  RELAY_SMTP_HOST    必填。府內 Mail Relay
  RELAY_SMTP_PORT    選填,預設 25
  RELAY_ALLOW        選填。允許的來源 IP,逗號分隔;空 = 不限(只靠 token,不建議)
  RELAY_TO_DOMAINS   選填。允許的收件網域,逗號分隔;空 = 不限。建議設府內網域,防止被拿去寄府外
  RELAY_BIND         選填,預設 0.0.0.0:443
  RELAY_CERT / RELAY_KEY  選填。給了就走 HTTPS;沒給只准綁 127.0.0.1(本機測試用)
  RELAY_MAX_MB       選填,預設 20。單封上限(含 base64 附件)
"""
import base64
import datetime
import hmac
import json
import mimetypes
import os
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        sys.exit(f"[mailrelay] 缺少環境變數 {name}")
    return v


TOKEN = _env("RELAY_TOKEN", required=True).encode()
FROM = _env("RELAY_FROM", required=True)
SMTP_HOST = _env("RELAY_SMTP_HOST", required=True)
SMTP_PORT = int(_env("RELAY_SMTP_PORT", "25"))
ALLOW = {x.strip() for x in _env("RELAY_ALLOW", "").split(",") if x.strip()}
TO_DOMAINS = {x.strip().lower().lstrip("@") for x in _env("RELAY_TO_DOMAINS", "").split(",") if x.strip()}
HOST, _, PORT = _env("RELAY_BIND", "0.0.0.0:443").rpartition(":")
CERT, KEY = _env("RELAY_CERT"), _env("RELAY_KEY")
MAX_BYTES = int(float(_env("RELAY_MAX_MB", "20")) * 1024 * 1024)

RE_EMAIL = re.compile(r"^[^@\s<>,;]+@([^@\s<>,;]+\.[^@\s<>,;]+)$")


def log(*a):
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def build_message(d):
    """驗證請求內容並組信。錯誤一律丟 ValueError(回 400)。"""
    if not isinstance(d, dict):
        raise ValueError("內容必須是 JSON 物件")
    to = d.get("to")
    if isinstance(to, str):
        to = [to]
    if not isinstance(to, list) or not to:
        raise ValueError("to 必須是非空的收件人清單")
    addrs = []
    for t in to:
        m = RE_EMAIL.match(str(t).strip())
        if not m:
            raise ValueError(f"收件人格式錯誤: {t!r}")
        if TO_DOMAINS and m.group(1).lower() not in TO_DOMAINS:
            raise ValueError(f"收件網域不在允許清單: {m.group(1)}")
        addrs.append(str(t).strip())
    subject = str(d.get("subject") or "").strip()
    html = d.get("html")
    if not subject or not isinstance(html, str):
        raise ValueError("subject 與 html 必填")

    msg = EmailMessage()
    msg["From"] = FROM
    msg["To"] = ", ".join(addrs)
    msg["Subject"] = subject
    msg.set_content("此信為 HTML 格式,請使用支援 HTML 的郵件軟體閱讀。")
    msg.add_alternative(html, subtype="html")
    atts = d.get("attachments") or []
    if not isinstance(atts, list) or not all(isinstance(a, dict) for a in atts):
        raise ValueError("attachments 必須是 {name, data} 物件的清單")
    for a in atts:
        name = os.path.basename(str(a.get("name") or "attachment"))
        data = base64.b64decode(a.get("data") or "", validate=True)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg, addrs


class Handler(BaseHTTPRequestHandler):
    server_version = "mailrelay"
    sys_version = ""

    def log_message(self, fmt, *args):  # 預設 access log 改由 log() 統一印
        pass

    def _reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self):
        ip = self.client_address[0]
        if ALLOW and ip not in ALLOW:
            # 印出看到的來源 IP:NAT 若改寫來源,把這個值加進 RELAY_ALLOW 即可
            log("403 來源不在允許清單", ip)
            self._reply(403, {"ok": False, "error": "forbidden", "source": ip})
            return False
        return True

    def do_GET(self):
        if self.path != "/health":
            return self._reply(404, {"ok": False, "error": "not found"})
        if self._allowed():
            self._reply(200, {"ok": True})

    def do_POST(self):
        ip = self.client_address[0]
        if self.path != "/send":
            return self._reply(404, {"ok": False, "error": "not found"})
        if not self._allowed():
            return
        auth = self.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(auth, b"Bearer " + TOKEN):
            log("401 token 錯誤", ip)
            return self._reply(401, {"ok": False, "error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BYTES:
            return self._reply(413, {"ok": False, "error": f"大小須在 1~{MAX_BYTES} bytes"})
        try:
            msg, addrs = build_message(json.loads(self.rfile.read(length)))
        except (ValueError, json.JSONDecodeError, base64.binascii.Error) as e:
            log("400", ip, e)
            return self._reply(400, {"ok": False, "error": str(e)})
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as s:
                refused = s.send_message(msg, from_addr=FROM, to_addrs=addrs)
        except (smtplib.SMTPException, OSError) as e:
            log("502 Mail Relay 錯誤", ip, type(e).__name__, e)
            return self._reply(502, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        log("200 已轉交", ip, f"收件人 {len(addrs)}", f"拒收 {len(refused)}")
        self._reply(200, {"ok": True, "refused": refused})


class Server(ThreadingHTTPServer):
    """TLS 改在每條連線的工作執行緒裡做:交握不卡住 accept,結束時送 close_notify 再關線
    (直接關線時 Windows curl/schannel 會判定失敗)。"""
    tls = None

    def finish_request(self, request, client_address):
        if not self.tls:
            return super().finish_request(request, client_address)
        request.settimeout(30)  # 交握與讀寫逾時,避免慢速連線一直佔住執行緒
        try:
            conn = self.tls.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError) as e:
            log("TLS 交握失敗", client_address[0], e)
            return
        try:
            super().finish_request(conn, client_address)
        finally:
            try:
                conn.settimeout(2)
                conn.unwrap().close()
            except (ssl.SSLError, OSError):
                conn.close()


def main():
    srv = Server((HOST, int(PORT)), Handler)
    if CERT and KEY:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(CERT, KEY)
        srv.tls = ctx
        scheme = "https"
    elif HOST in ("127.0.0.1", "localhost"):
        scheme = "http"
    else:
        sys.exit("[mailrelay] 沒有 RELAY_CERT/RELAY_KEY 時只准綁 127.0.0.1(不可明文對外)")
    log(f"mailrelay 啟動 {scheme}://{HOST}:{PORT}  允許來源 {sorted(ALLOW) or '不限'}  "
        f"收件網域 {sorted(TO_DOMAINS) or '不限'}")
    srv.serve_forever()


if __name__ == "__main__":
    main()

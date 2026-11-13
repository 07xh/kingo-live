#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/test_fingerprint.py - offline regression for item #14.

Loopback HTTP target only. No school host, no credentials, no write endpoint.
Python 3.10+ / standard library.

    python test_fingerprint.py -v
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import struct
import sys
import tempfile
import threading

import fingerprint as fp
import fingerprint_bridge as bridge


# ---------------------------------------------------------------------------
# loopback target that echoes exactly what it received
# ---------------------------------------------------------------------------

class EchoTarget(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _serve(self):
        rows = [[k, v] for k, v in self.headers.items()]
        body = json.dumps({
            "method": self.command, "path": self.path,
            "http_version": self.request_version,
            "headers": rows,
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _serve
    do_POST = _serve


class Case:
    def __init__(self):
        self.total = 0
        self.failed = []
        self.verbose = "-v" in sys.argv

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        self.total += 1
        if cond:
            if self.verbose:
                print(f"  OK  {name}")
        else:
            self.failed.append((name, detail))
            print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def sample_har() -> dict:
    return {"log": {"entries": [
        {"request": {
            "method": "GET",
            "url": "https://jwmis.ncwu.edu.cn/hsjw//wap/mycourseschedule.action"
                   "?xnxq=2026-2027-1&token=SECRET_TOKEN_ABC",
            "httpVersion": "HTTP/2.0",
            "headers": [
                {"name": ":method", "value": "GET"},
                {"name": "User-Agent", "value": "Mozilla/5.0 Chrome/126"},
                {"name": "Cookie", "value": "JSESSIONID=AAA; CAS_TOKEN=BBB"},
                {"name": "Authorization", "value": "Bearer SECRET_JWT"},
                {"name": "Referer", "value": "https://jwmis.ncwu.edu.cn/"},
            ],
            "queryString": [
                {"name": "xnxq", "value": "2026-2027-1"},
                {"name": "token", "value": "SECRET_TOKEN_ABC"},
            ],
        }, "response": {"httpVersion": "HTTP/2.0"}},
        {"request": {
            "method": "POST", "url": "https://api.xiqueer.com/manager//wap/wapController.jsp",
            "headers": [{"name": "Content-Type", "value": "application/x-www-form-urlencoded"}],
            "postData": {"params": [
                {"name": "kcid", "value": "TARGET_COURSE_ID"},
                {"name": "xqerSign", "value": "SIGN_VALUE"},
            ]},
        }, "response": {"httpVersion": "HTTP/1.1"}},
    ]}}


def client_hello() -> bytes:
    """Synthetic TLS1.2 ClientHello with GREASE, SNI, groups, points, ALPN."""
    def ext(etype: int, data: bytes) -> bytes:
        return struct.pack("!HH", etype, len(data)) + data

    body = struct.pack("!H", 0x0303) + bytes([1]) * 32 + bytes([0])
    body += struct.pack("!H", 6) + struct.pack("!HHH", 0x0A0A, 0xC02F, 0xC030)
    body += bytes([1, 0])
    exts = b"".join([
        ext(0x0000, struct.pack("!H", 5) + bytes([0]) +
            struct.pack("!H", 2) + b"ab"),
        ext(0x000A, struct.pack("!H", 4) + struct.pack("!HH", 0x0A0A, 0x001D)),
        ext(0x000B, bytes([1, 0])),
        ext(0x0A0A, b""),
        ext(0x0010, struct.pack("!H", 3) + bytes([2]) + b"h2"),
    ])
    body += struct.pack("!H", len(exts)) + exts
    hs = bytes([0x01]) + len(body).to_bytes(3, "big") + body
    return bytes([0x16]) + struct.pack("!H", 0x0301) + struct.pack("!H", len(hs)) + hs


def unit_baseline() -> fp.Fingerprint:
    return fp.Fingerprint(name="unit", headers=[
        ("Host", "TARGET_HOST"),
        ("User-Agent", "Mozilla/5.0 unit"),
        ("Accept", "application/json"),
        ("Cookie", "<cookie>"),
        ("X-Requested-With", "XMLHttpRequest"),
        ("Connection", "keep-alive"),
    ], cookie_names=["recon"])


class FakeReq:
    def __init__(self, url="https://jwmis.ncwu.edu.cn/hsjw//wap/x.action",
                 method="GET", headers=None, query=None, form=None):
        self.pretty_url, self.url, self.method = url, url, method
        self.headers = dict(headers or {})
        self.query = dict(query or {})
        self.urlencoded_form = dict(form or {})


class FakeFlow:
    def __init__(self, req):
        self.request = req
        self.killed = False

    def kill(self):
        self.killed = True


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def main() -> int:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), EchoTarget)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = Case()
    tmp = tempfile.mkdtemp(prefix="kingo-fp-")
    try:
        host, p = "127.0.0.1", port

        # --- redaction ---------------------------------------------------
        c.check("redact hides secret", "SECRET" not in fp.redact("SECRET"))
        c.check("redact deterministic", fp.redact("abc") == fp.redact("abc"))
        c.check("redact differs", fp.redact("abc") != fp.redact("abd"))
        c.check("redact len marker", "len=3" in fp.redact("abc"))
        c.check("is_redacted", fp.is_redacted(fp.redact("x")) and
                not fp.is_redacted("plain"))

        # --- HAR baseline ------------------------------------------------
        har_path = os.path.join(tmp, "capture.har")
        with open(har_path, "w", encoding="utf-8") as fh:
            json.dump(sample_har(), fh)
        har = fp.load_har(har_path)
        c.check("har_entries all", len(fp.har_entries(har)) == 2)
        c.check("har_entries url filter",
                len(fp.har_entries(har, "/hsjw//")) == 1)
        c.check("har_entries method filter",
                len(fp.har_entries(har, "", "POST")) == 1)
        c.check("har_entries no match",
                fp.har_entries(har, "/does-not-exist") == [])

        base = fp.fingerprint_from_har(har_path, "/hsjw//")
        c.check("har method", base.method == "GET")
        c.check("har path double slash", base.path.startswith("/hsjw//wap/"))
        c.check("har header order exact",
                [k for k, _ in base.headers] ==
                ["Host", "User-Agent", "Cookie", "Authorization", "Referer"],
                str([k for k, _ in base.headers]))
        c.check("har pseudo-headers stripped",
                any("pseudo-headers stripped" in n for n in base.notes),
                str(base.notes))
        c.check("har url query token scrubbed",
                "SECRET_TOKEN_ABC" not in base.url,
                base.url)
        c.check("har cookie redacted",
                all("JSESSIONID" not in v for k, v in base.headers
                    if k.lower() == "cookie"))
        c.check("har auth redacted",
                "SECRET_JWT" not in base.header_value("Authorization"))
        c.check("har cookie name order",
                base.cookie_names == ["JSESSIONID", "CAS_TOKEN"],
                str(base.cookie_names))
        c.check("har param names", "xnxq" in base.param_names)
        c.check("har sensitive params", base.sensitive_params == ["token"],
                str(base.sensitive_params))
        c.check("har h2 alpn", base.alpn == ["h2", "http/1.1"], str(base.alpn))
        c.check("har synthesised host", "Host header absent" in " ".join(base.notes),
                str(base.notes))
        c.check("har source/redacted", base.source == "har" and base.redacted)
        c.check("har postData params",
                "kcid" in fp.fingerprint_from_har(har_path, "xiqueer").param_names)
        c.check("har json no secrets",
                "SECRET_TOKEN_ABC" not in base.to_json() and
                "SECRET_JWT" not in base.to_json())
        c.check("har roundtrip",
                fp.Fingerprint.from_dict(base.to_dict()).headers == base.headers)
        try:
            fp.fingerprint_from_har(har_path, "/nope")
            c.check("har no match raises", False)
        except ValueError:
            c.check("har no match raises", True)
        try:
            fp.fingerprint_from_har(har_path, "", 9)
            c.check("har index raises", False)
        except IndexError:
            c.check("har index raises", True)

        # --- JA3 ---------------------------------------------------------
        raw = client_hello()
        j = fp.parse_client_hello(raw)
        c.check("ja3 version", j["ssl_version"] == 771, str(j["ssl_version"]))
        c.check("ja3 grease ciphers dropped",
                j["ciphers"] == [49199, 49200], str(j["ciphers"]))
        c.check("ja3 grease flagged", j["grease_present"] is True)
        c.check("ja3 extensions", j["extensions"] == [0, 10, 11, 16],
                str(j["extensions"]))
        c.check("ja3 curves", j["curves"] == [29], str(j["curves"]))
        c.check("ja3 point formats", j["point_formats"] == [0],
                str(j["point_formats"]))
        c.check("ja3 server_name", j["server_name"] == ["ab"], str(j["server_name"]))
        c.check("ja3 string", j["ja3"] == "771,49199-49200,0-10-11-16,29,0",
                j["ja3"])
        c.check("ja3 md5", j["ja3_hash"] ==
                hashlib.md5(j["ja3"].encode()).hexdigest())
        c.check("ja3 hex input", fp.parse_client_hello(raw.hex())["ja3"] == j["ja3"])
        try:
            fp.parse_client_hello(b"")
            c.check("ja3 empty raises", False)
        except ValueError:
            c.check("ja3 empty raises", True)
        try:
            fp.parse_client_hello(b"\x16\x03\x01\x00\x05\x02\x00\x00\x01\x00")
            c.check("ja3 non-hello raises", False)
        except ValueError:
            c.check("ja3 non-hello raises", True)

        # --- TLS profile -------------------------------------------------
        prof = fp.TlsProfile(ciphers=["ECDHE-RSA-AES128-GCM-SHA256"],
                             alpn=["h2", "http/1.1"], session_tickets=False)
        ctx = fp.build_ssl_context(prof)
        c.check("tls cipher applied", "ECDHE-RSA-AES128-GCM-SHA256" in
                [x["name"] for x in ctx.get_ciphers()])
        c.check("tls no ticket", bool(ctx.options & __import__("ssl").OP_NO_TICKET))
        c.check("tls verify on", ctx.check_hostname is True)
        c.check("tls verify off",
                fp.build_ssl_context(fp.TlsProfile(verify=False)).check_hostname is False)
        c.check("tls session connection class",
                hasattr(fp.SessionHTTPSConnection, "connect"))

        # --- AlignedClient against loopback ------------------------------
        base_u = unit_baseline()
        client = fp.AlignedClient(host, p, "http", baseline=base_u,
                                  timeout=3.0, cookie="recon=1")
        r1 = client.request("GET", "/hsjw//wap/mycourseschedule.action")
        c.check("aligned status", r1.status == 200, str(r1.status))
        echo = r1.echo
        sent_names = [k.lower() for k, _ in echo.get("headers", [])]
        c.check("aligned server saw exact order",
                sent_names == base_u.header_names(), str(sent_names))
        c.check("aligned host rewritten",
                any(k.lower() == "host" and v == f"{host}:{p}"
                    for k, v in echo.get("headers", [])), str(echo.get("headers", [])[:2]))
        c.check("aligned no identity encoding",
                not any(k.lower() == "accept-encoding" and v == "identity"
                        for k, v in echo.get("headers", [])))
        c.check("aligned cookie injected",
                any(k.lower() == "cookie" and v == "recon=1"
                    for k, v in echo.get("headers", [])))
        c.check("aligned public redacts cookie",
                any(k.lower() == "cookie" and v == "<injected>"
                    for k, v in r1.headers_sent))
        c.check("aligned double slash preserved",
                echo.get("path") == "/hsjw//wap/mycourseschedule.action",
                str(echo.get("path")))
        c.check("aligned echo parsed", r1.echo.get("headers") is not None)
        r2 = client.request("GET", "/hsjw//wap/mycourseschedule.action")
        c.check("aligned keep-alive reused", r2.reused is True)
        c.check("aligned ttfb measured", r1.ttfb_ms >= 0.0)
        try:
            client.request("POST", "/submit")
            c.check("aligned write blocked", False)
        except PermissionError:
            c.check("aligned write blocked", True)
        try:
            client.request("HEAD", "/hsjw//wap/mycourseschedule.action")
            c.check("aligned head allowed", True)
        except PermissionError:
            c.check("aligned head allowed", False)
        client.close()

        # allow_write=True really sends (still a loopback echo, no real POST)
        w = fp.AlignedClient(host, p, "http", baseline=base_u,
                             cookie="recon=1", allow_write=True)
        rw = w.request("POST", "/echo", body="a=1")
        c.check("aligned write explicit ok", rw.status == 200 and
                rw.echo.get("method") == "POST", str(rw.status))
        w.close()

        # no cookie -> redacted Cookie header must be dropped, never sent
        nc = fp.AlignedClient(host, p, "http", baseline=base_u, cookie_env="")
        rn = nc.request("GET", "/probe")
        c.check("aligned drops placeholder cookie",
                not any(k.lower() == "cookie" for k, _ in rn.echo.get("headers", [])))
        c.check("aligned no cookie in public",
                not any(k.lower() == "cookie" for k, _ in rn.headers_sent))
        nc.close()

        # default header set / extra header position
        d = fp.AlignedClient(host, p, "http", cookie_env="")
        rd = d.request("GET", "/probe", extra_headers={"Accept": "text/plain"})
        names = [k.lower() for k, _ in rd.echo.get("headers", [])]
        c.check("default headers include browser set",
                {"user-agent", "sec-fetch-mode", "accept-language"} <= set(names),
                str(names))
        c.check("extra header stays at baseline position",
                names.index("accept") == 2, str(names))
        d.close()

        # probe_alignment diff
        pc = fp.AlignedClient(host, p, "http", baseline=base_u, cookie="recon=1")
        al = fp.probe_alignment(pc, "/hsjw//wap/mycourseschedule.action")
        c.check("probe_alignment aligned", al["aligned"] is True,
                str(al.get("diff", {}).get("critical")))
        c.check("probe_alignment order match",
                al["diff"]["header_order_match"] is True)
        c.check("probe_alignment has baseline", "observed" in al)
        pc.close()

        # --- compare / gate ----------------------------------------------
        same = fp.compare(base_u, base_u)
        c.check("compare identical clean", same["critical"] == [] and
                same["alignment_score"] == 1.0, str(same["critical"]))
        obs_bad = fp.Fingerprint(name="bad", headers=[
            ("Accept", "application/json"), ("Host", "TARGET_HOST"),
            ("User-Agent", "curl/8.0"), ("Cookie", fp.redact("recon=1")),
            ("X-Foo", "1"),
        ], cookie_names=["recon"])
        diff = fp.compare(base_u, obs_bad)
        c.check("compare order mismatch", diff["header_order_match"] is False)
        c.check("compare critical order", "header_order" in diff["critical"])
        c.check("compare critical UA value", "value:user-agent" in diff["critical"],
                str(diff["critical"]))
        c.check("compare missing connection", "missing:connection" in diff["cosmetic"],
                str(diff["cosmetic"]))
        c.check("compare extra header", "extra:x-foo" in diff["cosmetic"])
        c.check("compare score bounded", 0.0 <= diff["alignment_score"] <= 1.0)
        obs_cookie = fp.Fingerprint(name="cookie", headers=list(base_u.headers),
                                    cookie_names=["CAS_TOKEN", "JSESSIONID"])
        c.check("compare cookie order critical",
                "cookie_name_order" in fp.compare(base_u, obs_cookie)["critical"])
        obs_alpn = fp.Fingerprint(name="alpn", headers=list(base_u.headers),
                                  cookie_names=["recon"], alpn=["h2"])
        c.check("compare alpn critical (h1 baseline)",
                "alpn" in fp.compare(
                    fp.Fingerprint(name="b", headers=list(base_u.headers),
                                   cookie_names=["recon"], alpn=["h2"]),
                    fp.Fingerprint(name="o", headers=list(base_u.headers),
                                   cookie_names=["recon"], alpn=["http/1.1"]))["critical"])
        c.check("compare ja3 critical",
                "ja3" in fp.compare(
                    fp.Fingerprint(name="b", headers=[], ja3_hash="a" * 32),
                    fp.Fingerprint(name="o", headers=[], ja3_hash="b" * 32))["critical"])
        c.check("compare order-insensitive mode",
                "header_order" not in fp.compare(base_u, obs_bad, False)["critical"])

        g1 = fp.assess_gate({"raw_status": 403, "browser_status": 200})
        c.check("gate divergent", g1["verdict"] == "use_browser_channel" and
                g1["channel"] == "cdp", str(g1))
        c.check("gate divergent evidence", bool(g1["evidence"]))
        c.check("gate divergent actions", any("CDPReplay" in a for a in g1["actions"]))
        g2 = fp.assess_gate({"raw_status": 200, "browser_status": 200,
                             "js_challenge": True})
        c.check("gate js challenge", g2["gate_present"] is True and
                g2["channel"] == "cdp")
        g3 = fp.assess_gate({"raw_status": 200, "browser_status": 200,
                             "captcha": True})
        c.check("gate captcha human", g3["verdict"] == "human_required" and
                g3["channel"] == "none")
        g4 = fp.assess_gate({"raw_status": 200, "browser_status": 200}, diff)
        c.check("gate align required", g4["verdict"] == "align_required", str(g4))
        g5 = fp.assess_gate({"raw_status": 200, "browser_status": 200}, same)
        c.check("gate pass", g5["verdict"] == "pass")
        c.check("gate unknown", fp.assess_gate({})["verdict"] == "unknown")
        c.check("gate tls challenge",
                fp.assess_gate({"tls_challenge": True})["channel"] == "cdp")
        c.check("demo serialisable", len(json.dumps(fp.demo())) > 200)

        # --- websocket frames --------------------------------------------
        short = bridge.encode_frame(b'{"a":1}', key=b"abcd")
        op, payload, used = bridge.decode_frame(short)
        c.check("frame roundtrip", op == 1 and payload == b'{"a":1}' and
                used == len(short))
        medium = bridge.encode_frame(b"x" * 200, key=b"abcd")
        c.check("frame 16-bit length",
                bridge.decode_frame(medium)[1] == b"x" * 200)
        unmasked = bridge.encode_frame(b"plain", mask=False)
        c.check("frame unmasked", bridge.decode_frame(unmasked)[1] == b"plain")
        try:
            bridge.encode_frame(b"x", key=b"abc")
            c.check("frame bad key raises", False)
        except ValueError:
            c.check("frame bad key raises", True)
        try:
            bridge.decode_frame(b"\x81")
            c.check("frame short raises", False)
        except ValueError:
            c.check("frame short raises", True)

        # --- CDP expression / targets ------------------------------------
        expr = bridge.build_fetch_expression(
            "https://h/p?xnxq=2026-2027-1", "GET")
        c.check("cdp expr has url", "p?xnxq=2026-2027-1" in expr)
        c.check("cdp expr include credentials", "credentials" in expr and
                '"include"' in expr)
        c.check("cdp expr GET", '"method": "GET"' in expr)
        c.check("cdp expr awaitPromise path",
                "await fetch(" in expr and "r.status" in expr)
        inj = bridge.build_fetch_expression('https://h/a"b</script>')
        c.check("cdp expr escapes quotes", '\\"b' in inj)
        c.check("cdp expr body", '"body": "a=1"' in
                bridge.build_fetch_expression("https://h/p", "POST", body="a=1"))
        targets = [{"type": "page", "url": "https://jwmis.ncwu.edu.cn/hsjw//",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1"},
                   {"type": "page", "url": "https://example.com/",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/2"}]
        c.check("cdp pick target", bridge.pick_target(targets, "/hsjw//")["url"]
                .endswith("/hsjw//"))
        c.check("cdp pick first", bridge.pick_target(targets)["type"] == "page")
        try:
            bridge.pick_target([], "x")
            c.check("cdp no target raises", False)
        except RuntimeError:
            c.check("cdp no target raises", True)
        rep = object.__new__(bridge.CDPReplay)
        rep.allow_write, rep.ws = False, None
        try:
            rep.fetch("https://h/p", "POST")
            c.check("cdp write blocked", False)
        except PermissionError:
            c.check("cdp write blocked", True)

        # --- mitmproxy addon ---------------------------------------------
        addon = bridge.FingerprintInjection(url_contains="/hsjw//",
                                            overrides={"kcid": "C_NEW"})
        addon.baseline = base_u
        f1 = FakeFlow(FakeReq(headers={"Host": "jwmis.ncwu.edu.cn",
                                       "Cookie": "recon=1",
                                       "Accept": "application/json"},
                              query={"kcid": "C_OLD"}))
        addon.request(f1)
        c.check("addon header order restored",
                list(f1.request.headers.keys()) ==
                ["Host", "User-Agent", "Accept", "Cookie",
                 "X-Requested-With", "Connection"],
                str(list(f1.request.headers.keys())))
        c.check("addon keeps live cookie",
                f1.request.headers.get("Cookie") == "recon=1",
                str(f1.request.headers.get("Cookie")))
        c.check("addon rewrites kcid", f1.request.query["kcid"] == "C_NEW")
        c.check("addon logged", bool(addon.log))
        f2 = FakeFlow(FakeReq(method="POST", query={"kcid": "C_OLD"}))
        addon.request(f2)
        c.check("addon blocks write", f2.killed is True)
        f3 = FakeFlow(FakeReq(query={"kcid": "C_OLD", "xqerSign": "SIG"}))
        addon.request(f3)
        c.check("addon skips signed request", f3.request.query["kcid"] == "C_OLD")
        c.check("addon logs signed warning",
                any("signed request" in x for x in addon.log))
        f4 = FakeFlow(FakeReq(url="https://other.example/", query={"kcid": "X"}))
        addon.request(f4)
        c.check("addon url filter", f4.request.query["kcid"] == "X")
        bp = os.path.join(tmp, "fp.json")
        with open(bp, "w", encoding="utf-8") as fh:
            fh.write(base_u.to_json())
        loaded = bridge.FingerprintInjection(baseline_path=bp)
        c.check("addon loads baseline file",
                loaded.baseline is not None and loaded.baseline.name == "unit")
        c.check("addons constant read-only", bridge.addons[0].allow_write is False)

        st = bridge.selftest()
        c.check("bridge selftest frame", st["websocket_frame_roundtrip"] is True)
        c.check("bridge selftest readonly", st["read_only_default"] is True)
        c.check("bridge selftest serialisable", len(json.dumps(st)) > 100)
    finally:
        srv.shutdown()
        srv.server_close()

    passed = c.total - len(c.failed)
    print(f"{passed}/{c.total} OK")
    for name, detail in c.failed:
        print(f"  FAILED: {name} :: {detail}")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/test_phase2.py - offline regression for #4 #5 #6 #7 #8 #9 #13.

Loopback HTTP target + pure logic checks. No school host, no credentials, no
write requests. Standard library only, Python 3.10+.
"""
from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import endpoints
import preflight
import crosscheck
import signature


class C:
    def __init__(self) -> None:
        self.n = 0
        self.bad: list = []

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        self.n += 1
        if not cond:
            self.bad.append((name, detail))

    def done(self) -> int:
        for name, detail in self.bad:
            print("FAIL", name, detail)
        print(f"{self.n - len(self.bad)}/{self.n} OK")
        return 1 if self.bad else 0


# ---------------------------------------------------------------------------
# loopback target: three distinct response shapes
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):                        # noqa: D401
        pass

    def do_GET(self):                                 # noqa: N802
        p = self.path
        if "/api/" in p or "format=json" in p:
            body, status, rate = b'{"prohibit":"0","rows":[]}', 200, "600"
        elif "//" in p:
            body, status, rate = b'{"prohibit":"1","msg":"not in selection time"}', 200, "60"
        elif p.endswith(".do"):
            body, status, rate = b"<html>not in selection time</html>", 200, "60"
        else:
            body, status, rate = b"<html>not in selection time</html>", 403, "60"
        self.send_response(status)
        self.send_header("Content-Type", "application/json" if b"{" in body else "text/html")
        self.send_header("X-RateLimit-Limit", rate)
        self.send_header("Server-Timing", "queue;dur=3")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def loopback_probe(c: C) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]
    eps = endpoints.build_matrix("/hsjw/wap/mycourseschedule.action",
                                 param_sets=[{"xnxq": "2026-2027-1"}])

    def fetch(e: endpoints.Endpoint) -> endpoints.SimpleResponse:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
        try:
            conn.request("GET", e.url())
            r = conn.getresponse()
            return endpoints.SimpleResponse(r.status, dict(r.getheaders()),
                                            r.read(), 1.0)
        finally:
            conn.close()

    try:
        results = endpoints.probe_all(fetch, eps)
        cls = endpoints.classify(results)
        c.check("loopback probed all", len(results) == len(eps), str(len(results)))
        c.check("loopback no errors", not cls["errors"], str(cls["errors"]))
        c.check("loopback has 200", cls["ok"] > 0, str(cls["ok"]))
        c.check("loopback divergent", cls["divergent"], str(cls["groups"]))
        c.check("loopback independent", cls["independent_entrance"])
        c.check("loopback rate buckets >= 2", cls["rate_buckets"] >= 2,
                str(cls["rate_buckets"]))
        c.check("loopback markers differ", len(cls["window_markers"]) >= 2,
                str(cls["window_markers"]))
        c.check("loopback api open", any(r.window_marker == "open" for r in results))
        c.check("loopback dblslash closed",
                any(r.window_marker == "closed" for r in results))
        c.check("loopback captures rate headers",
                any("x-ratelimit-limit" in r.rate for r in results))
        c.check("loopback captures server-timing",
                any("server-timing" in r.rate for r in results))
        rec = endpoints.recommend(cls, {"attribution": "server_time"})
        c.check("loopback recommend one channel", bool(rec["channel"]))
        c.check("loopback concurrency stays 1", rec["concurrency"] == 1)
        c.check("loopback single submit", rec["single_physical_submit"] is True)
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)


# ---------------------------------------------------------------------------
# #4 #6 endpoints
# ---------------------------------------------------------------------------

def test_endpoints(c: C) -> None:
    base = "/hsjw/wap/mycourseschedule.action"
    eps = endpoints.build_matrix(base, param_sets=[{"xnxq": "2026-2027-1"}])
    keys = [e.key() for e in eps]
    c.check("matrix non-empty", len(eps) >= 8, str(len(eps)))
    c.check("matrix unique", len(keys) == len(set(keys)))
    c.check("matrix has wap", any(e.channel == "wap" for e in eps))
    c.check("matrix has web", any(e.channel == "web" for e in eps))
    c.check("matrix has api", any(e.channel == "api" for e in eps))
    c.check("matrix .do variant", any(e.path.endswith(".do") for e in eps))
    c.check("matrix .action variant", any(e.path.endswith(".action") for e in eps))
    c.check("matrix double slash", any("//" in e.path for e in eps))
    c.check("matrix json channel", any("json" in e.name for e in eps))
    c.check("matrix paramset", any("paramset" in e.name for e in eps))
    c.check("json Accept header", any(e.headers.get("Accept") == "application/json"
                                     for e in eps))
    c.check("url encodes params", "xnxq=2026-2027-1" in eps[-1].url())
    c.check("all methods read-only",
            all(e.method in endpoints.READ_ONLY_METHODS for e in eps))
    c.check("public has no values", "params" not in eps[0].public())

    cfg = json.loads((Path(__file__).with_name("config.example.json"))
                     .read_text(encoding="utf-8"))
    from_cfg = endpoints.matrix_from_config(cfg)
    c.check("matrix_from_config non-empty", len(from_cfg) > len(eps))
    c.check("matrix_from_config unique",
            len({e.key() for e in from_cfg}) == len(from_cfg))

    # read-only invariant
    bad = endpoints.Endpoint("post", "web", "/x", method="POST")
    try:
        endpoints.probe_all(lambda e: endpoints.SimpleResponse(), [bad])
        c.check("write method blocked", False)
    except ValueError:
        c.check("write method blocked", True)
    ok = endpoints.probe_all(lambda e: endpoints.SimpleResponse(), [bad],
                             allow_write=True)
    c.check("allow_write is caller-owned", len(ok) == 1)

    # error isolation
    def boom(e):
        raise RuntimeError("target down")
    err = endpoints.probe_all(boom, eps[:2])
    c.check("fetch error isolated", all(r.error == "RuntimeError" for r in err))
    c.check("error result status 0", all(r.status == 0 for r in err))

    # markers
    c.check("marker closed json", endpoints._marker('{"prohibit":"1"}') == "closed")
    c.check("marker open json", endpoints._marker('{"prohibit":"0"}') == "open")
    c.check("marker chinese closed", endpoints._marker("不在选课时间") == "closed")
    c.check("marker unknown", endpoints._marker("hello") == "unknown")

    # classification on identical responses
    same = endpoints.probe_all(
        lambda e: endpoints.SimpleResponse(200, {"Content-Type": "text/html"},
                                           b"hello"), eps[:4])
    sc = endpoints.classify(same)
    c.check("identical not divergent", not sc["divergent"])
    c.check("identical not independent", not sc["independent_entrance"])
    c.check("identical single group", len(sc["groups"]) == 1)

    # window attribution
    o1 = [endpoints.WindowObservation("a", "2026-2027-1", "same", "closed"),
          endpoints.WindowObservation("b", "2025-2026-2", "same", "open")]
    a1 = endpoints.attribute_window(o1)
    c.check("param attribution", a1["attribution"] == "client_param", str(a1))
    c.check("param confidence", a1["confidence"] >= 0.8)
    o2 = [endpoints.WindowObservation("a", "t", "same", "closed"),
          endpoints.WindowObservation("b", "t", "other", "open")]
    c.check("session attribution",
            endpoints.attribute_window(o2)["attribution"] == "session_state")
    o3 = [endpoints.WindowObservation("a", "t", "same", "closed"),
          endpoints.WindowObservation("b", "u", "other", "closed")]
    c.check("server_time attribution",
            endpoints.attribute_window(o3)["attribution"] == "server_time")
    o4 = [endpoints.WindowObservation("a", "t", "same", "open", True)]
    c.check("js_side attribution",
            endpoints.attribute_window(o4)["attribution"] == "js_side")
    c.check("empty attribution unknown",
            endpoints.attribute_window([])["attribution"] == "unknown")
    c.check("attribution evidence present", bool(a1["evidence"]))

    # recommendation
    empty = endpoints.classify([])
    c.check("no live channel -> empty rec",
            endpoints.recommend(empty, {})["channel"] == "")
    c.check("empty rec keeps single submit",
            endpoints.recommend(empty, {})["single_physical_submit"] is True)

    d = endpoints.demo()
    c.check("demo has classification", "classification" in d)
    c.check("demo has window", "window" in d)
    c.check("demo has recommendation", "recommendation" in d)
    c.check("demo independent", d["classification"]["independent_entrance"])
    c.check("demo gate is param", d["window"]["attribution"] == "client_param")
    c.check("demo json serialisable", bool(json.dumps(d, ensure_ascii=False)))
    c.check("run demo-fetch offline",
            endpoints.run(type("A", (), {"demo_fetch": True, "path": base,
                                         "window_param": "xnxq",
                                         "window_value": "", "host": "",
                                         "port": 443, "scheme": "https",
                                         "cookie_env": "KINGO_COOKIE",
                                         "timeout": 3.0})()) is not None)


# ---------------------------------------------------------------------------
# #7 #8 #9 preflight
# ---------------------------------------------------------------------------

def test_preflight(c: C) -> None:
    html = ('<input type="hidden" name="token" value="T0k3n" />'
            '<input value="lt-9" name="lt" />'
            '<input id="xktype" value="N" />'
            '<input name="empty" value="" />')
    hidden = preflight.extract_hidden(html)
    c.check("extract name-first", hidden.get("token") == "T0k3n")
    c.check("extract value-first", hidden.get("lt") == "lt-9")
    c.check("extract id fallback", hidden.get("xktype") == "N")
    c.check("extract empty ignored later", hidden.get("empty") == "")

    pool = preflight.TicketPool(ttl_s=1.0)
    added = pool.harvest(html, ("token", "lt", "xktype", "empty"))
    c.check("harvest 3 tickets", len(added) == 3, str(added))
    rep = pool.report()
    c.check("report has no raw value", "T0k3n" not in json.dumps(rep))
    c.check("report item has sha8", rep["items"][0]["sha8"])
    c.check("take token once", pool.take("token") == "T0k3n")
    c.check("take token twice blocked", pool.take("token") is None)
    c.check("report marks consumed", any(i["consumed"] for i in pool.report()["items"]))
    c.check("purge removes consumed", pool.purge() == 1)
    c.check("available decreases", pool.report()["available"] == 2)
    old = preflight.Ticket("old", "v", time.time() - 100, ttl_s=1.0)
    c.check("ticket expired", old.expired())
    pool.put("fresh", "F")
    c.check("fresh take works", pool.take("fresh") == "F")
    c.check("missing take None", pool.take("nope") is None)

    bodies = preflight.BodyPrebuilder({"xktype": "N", "sfbd": "0"})
    body = bodies.prebuild("t1", {"kcdm": "C1", "skbj": "01"})
    c.check("body has kcdm", "kcdm=C1" in body)
    c.check("body has skbj", "skbj=01" in body)
    c.check("body keeps template", "xktype=N" in body)
    c.check("body urlencoded", "+" not in body or "%" in body or body)
    c.check("body sealed", bodies.sealed("t1") == body)
    c.check("body report hides values", bodies.report()["values_in_report"] is False)
    c.check("body report bytes", bodies.report()["bytes"]["t1"] == len(body))

    shards = preflight.ConnectionShard(["10.0.0.1", "10.0.0.2"], 3, 1)
    sr = shards.report()
    c.check("shard count = sessions+spare", len(sr["shards"]) == 4)
    c.check("shard nodes round-robin",
            sr["shards"][2]["node"] == "10.0.0.1")
    c.check("spare disables tls reuse",
            sr["shards"][3]["tls_session_reuse"] is False)
    c.check("shards keep alive", all(s["keep_alive"] for s in sr["shards"]))
    c.check("early data blocked", sr["early_data_blocked"] is True)
    tlsp = shards.tls_plan()
    c.check("tls plan blocks early write", tlsp["early_data_for_write"] is False)
    c.check("tls plan session reuse", tlsp["session_reuse"] is True)
    c.check("tls plan alpn", "h2" in tlsp["alpn"])
    ips = shards.resolve("localhost", 80)
    c.check("dns resolve returns list", isinstance(ips, list) and bool(ips))
    c.check("dns resolve no error", not any("failed" in x for x in ips))

    clock = preflight.HighResClock()
    c.check("clock resolution positive", clock.resolution_ns() > 0)
    n1, n2 = clock.now_ns(), clock.now_ns()
    c.check("clock monotonic", n2 >= n1)
    ph = clock.phase_from_recon({"offset_ms": 1.0, "second_phase_ms": 25.0,
                                 "rtt_p50_ms": 18.0})
    c.check("phase reused from recon", ph["second_phase_ms"] == 25.0)
    c.check("phase source marked", ph["source"] == "recon.probe_clock")
    c.check("date precision honest", ph["date_precision_ms"] == 1000)
    w = clock.wait_until(clock.now_ns() + 3_000_000, lead_ms=1.0,
                         spin_ms=1.0, max_wait_s=1.0)
    c.check("wait not timed out", not w["timed_out"])
    c.check("wait leaves lead", w["remaining_ms"] <= 3.1, str(w))
    pin = clock.pin(0)
    c.check("pin returns dict", isinstance(pin, dict) and "applied" in pin)
    c.check("pin has warning", bool(pin["warning"]))

    plan = preflight.assemble(pool, bodies, shards, clock,
                              {"offset_ms": 1.0, "second_phase_ms": 25.0,
                               "rtt_p50_ms": 18.0})
    c.check("plan tickets available", plan["checks"]["tickets_available"])
    c.check("plan body preserialised", plan["checks"]["body_preserialised"])
    c.check("plan connections", plan["checks"]["connections_planned"])
    c.check("plan phase known", plan["checks"]["phase_known"])
    c.check("plan early data off", plan["checks"]["early_data_for_write"] is False)
    c.check("plan invariants single submit",
            plan["invariants"]["single_physical_submit"] is True)
    c.check("plan invariants 200 != success",
            plan["invariants"]["http_200_is_not_success"] is True)
    c.check("plan warnings", len(plan["warnings"]) >= 3)
    c.check("plan json serialisable", bool(json.dumps(plan, default=str)))

    d = preflight.demo()
    c.check("demo harvested", len(d["harvested"]) == 4, str(d["harvested"]))
    c.check("demo body has kcdm", d["body_contains_kcdm"])
    c.check("demo dns", bool(d["dns_nodes"]))
    c.check("demo wait", "remaining_ms" in d["wait"])
    c.check("demo plan", "checks" in d["plan"])
    c.check("demo json serialisable", bool(json.dumps(d, default=str)))


# ---------------------------------------------------------------------------
# #13 crosscheck
# ---------------------------------------------------------------------------

def test_crosscheck(c: C) -> None:
    t = {"course_id": "C1", "class_id": "01"}
    e = crosscheck.Evidence
    stale_neg = e("official_list", "C1", "01", False, 40.0, True, "cached")
    wap_pos = e("wap_schedule", "C1", "01", True, 55.0, False, "wap")
    credit_pos = e("credit_summary", "C1", "01", True, 61.0, False, "credit")
    zxxq_pos = e("zxxq", "C1", "01", True, 70.0, False, "zxxq")
    fresh_neg = e("official_list", "C1", "01", False, 38.0, False, "fresh")

    v = crosscheck.fuse(t, [stale_neg, wap_pos, credit_pos])
    c.check("stale list + 2 positives -> confirmed", v.verdict == "confirmed", str(v))
    c.check("confirmed is terminal", v.action == "terminal")
    c.check("confirmed lists stale channel", "official_list" in v.stale_channels)
    c.check("confirmed confidence high", v.confidence >= 0.7)

    v2 = crosscheck.fuse(t, [fresh_neg, wap_pos])
    c.check("fresh negative + positive -> contradicted",
            v2.verdict == "contradicted", str(v2))
    c.check("contradicted -> human_check", v2.action == "human_check")

    v3 = crosscheck.fuse(t, [stale_neg])
    c.check("only stale negative -> pending", v3.verdict == "pending")
    c.check("stale negative keeps pending", v3.action == "keep_pending")

    v4 = crosscheck.fuse(t, [fresh_neg])
    c.check("all fresh negative -> pending", v4.verdict == "pending")
    c.check("never promote to confirmed", v4.verdict != "confirmed")

    v5 = crosscheck.fuse(t, [])
    c.check("no evidence -> unknown", v5.verdict == "unknown")
    c.check("unknown keeps pending", v5.action == "keep_pending")

    v6 = crosscheck.fuse(t, [e("official_list", "C1", "01", True, 30.0, False, "fresh")])
    c.check("fresh official positive -> confirmed", v6.verdict == "confirmed")

    v7 = crosscheck.fuse(t, [stale_neg, zxxq_pos])
    c.check("weak partial positive stays pending", v7.verdict == "pending", str(v7))

    c.check("stale penalty applied",
            crosscheck._weight(stale_neg) < crosscheck._weight(fresh_neg))
    plan = crosscheck.channel_plan()
    c.check("plan 4 channels", len(plan["channels"]) == 4)
    c.check("plan read-only", plan["all_read_only"] is True)

    decisions = crosscheck.reconcile([t, {"course_id": "C2", "class_id": "02"}],
                                    [stale_neg, wap_pos, credit_pos, fresh_neg])
    c.check("reconcile one decision per intent", len(decisions) == 2)
    c.check("reconcile never emits submit",
            all(d.kind != "submit" for d in decisions))
    c.check("reconcile no fire action", all(d.action != "fire" for d in decisions))
    c.check("reconcile decisions serialisable",
            all(isinstance(d.as_dict(), dict) for d in decisions))

    d = crosscheck.demo()
    c.check("demo 3 verdicts", len(d["verdicts"]) == 3)
    c.check("demo 3 decisions", len(d["decisions"]) == 3)
    c.check("demo has plan", "plan" in d)
    c.check("demo json serialisable", bool(json.dumps(d)))


# ---------------------------------------------------------------------------
# #5 signature
# ---------------------------------------------------------------------------

def test_signature(c: C) -> None:
    r = signature.SIGN_REQUEST
    c.check("sign host", r["host"] == "api.xiqueer.com")
    c.check("sign path double slash", "//" in r["path"])
    c.check("sign is POST", r["method"] == "POST")
    c.check("sign fields", "xqerSign" in r["signed_fields"])
    c.check("identity fields", r["identity_fields"] == ["kcid", "skbj"])
    c.check("signed posts count", r["observed_signed_posts"] == 53)

    with tempfile.TemporaryDirectory(prefix="ops-sig-test-") as td:
        root = Path(td)
        (root / "Signer.smali").write_text(
            ".class public Lcom/xq/Signer;\n"
            ".method public static sign(Ljava/util/Map;)V\n"
            '  const-string v0, "xqerSign"\n'
            '  const-string v1, "encrptSecretKey"\n'
            ".end method\n", encoding="utf-8")
        (root / "Param.java").write_text(
            "class Param { void build() {\n"
            '  m.put("kcid", "C1"); m.put("skbj", "01");\n'
            '  m.put("param2", "https://example.org/very/long/token/value/here");\n'
            "}}\n", encoding="utf-8")
        hits = signature.locate_constructors(str(root))
        terms = {h.term for h in hits}
        c.check("locate finds kcid", "kcid" in terms, str(terms))
        c.check("locate finds xqerSign", "xqerSign" in terms)
        c.check("locate finds param2", "param2" in terms)
        c.check("locate smali class", any(h.class_name == "Lcom/xq/Signer"
                                          for h in hits))
        c.check("locate java class", any(h.class_name == "Param" for h in hits))
        c.check("locate redacts URL",
                all("<url>" in h.snippet or "https://" not in h.snippet
                    for h in hits))
        c.check("locate redacts long literal",
                all(len(h.snippet) <= 120 for h in hits))
        ranked = signature.rank(hits)
        c.check("rank total hits", ranked["total_hits"] == len(hits))
        c.check("rank best file", bool(ranked["best"]))
        c.check("rank candidate sorted",
                ranked["candidate_files"][0]["distinct_terms"] >= 1)

    empty = signature.locate_constructors("C:/definitely/not/here/ops-sig")
    c.check("missing root -> empty", empty == [])

    hook = signature.build_hook("C1", "01", "com.xq.Param", "build")
    c.check("hook contains kcid target", 'TARGET_KCID = "C1"' in hook)
    c.check("hook contains skbj target", 'TARGET_SKBJ = "01"' in hook)
    c.check("hook inserts class", 'Java.use("com.xq.Param")' in hook)
    c.check("hook inserts method", "build.implementation" in hook)
    c.check("hook patches kcid", 'key === "kcid"' in hook)
    c.check("hook patches skbj", 'key === "skbj"' in hook)
    c.check("hook never assigns xqerSign", "xqerSign =" not in hook)
    c.check("hook never assigns token", "token =" not in hook)
    c.check("hook documents no exit rewrite", "Never modify xqerSign" in hook)
    c.check("hook has HashMap container", "java.util.HashMap" in hook)
    c.check("hook has JSONObject container", "org.json.JSONObject" in hook)
    c.check("hook escaping", '\\"' in signature.build_hook('a"b', "01"))

    p = signature.policy()
    c.check("policy principle", "let the app sign" in p["principle"])
    c.check("policy explains exit rewrite", "invalidates" in p["why_not_exit_rewrite"])
    c.check("policy never touch signed", p["never_touch"] == r["signed_fields"])
    c.check("policy change only", p["change_only"] == ["kcid", "skbj"])
    c.check("policy dead end", "device fingerprint" in p["dead_end"])
    c.check("policy guards no replay", p["guards"]["no_production_replay"] is True)
    c.check("policy guards no algorithm",
            p["guards"]["no_algorithm_reimplementation"] is True)
    c.check("policy guards no write",
            p["guards"]["no_write_requests_from_this_module"] is True)
    c.check("policy guards single submit", p["guards"]["single_physical_submit"] is True)

    d = signature.demo()
    c.check("demo hits", len(d["hits"]) >= 4)
    c.check("demo ranked", "best" in d["ranked"])
    c.check("demo hook preview", len(d["hook_preview"]) > 0)
    c.check("demo hook len", d["hook_len"] > 500)
    c.check("demo no signed field edit",
            d["hook_mentions_signed_field_edit"] is False)
    c.check("demo json serialisable", bool(json.dumps(d)))


def main() -> int:
    c = C()
    test_endpoints(c)
    test_preflight(c)
    test_crosscheck(c)
    test_signature(c)
    loopback_probe(c)
    return c.done()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/signature.py - #5 mobile signed-channel constructor location (#5 only).

The xiqueer app signs its POSTs. Rewriting the outgoing body is useless: the
signature covers param/param2/token, so any exit-side edit fails validation.
The cheap path is to let the app sign the request itself and change the course
identifiers at the parameter-construction point.

  SIGN_REQUEST       observed endpoint / fields / identity fields (placeholders)
  locate_constructors  scan jadx/smali output for the construction points
  build_hook           emit a Frida script that edits kcid/skbj BEFORE signing
                       and never touches xqerSign / encrptSecretKey / token
  policy               why exit rewriting fails, evidence criteria, dead ends

This module performs static analysis only. It never sends a request, never
replays a production packet, and contains no signing algorithm. Targets are
TARGET_* placeholders until a real APK + HAR are supplied.

Standard library only, Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SIGN_REQUEST: Dict[str, Any] = {
    "host": "api.xiqueer.com",
    "path": "/manager//wap/wapController.jsp",
    "method": "POST",
    "observed_signed_posts": 53,
    "signed_fields": ["xqerSign", "encrptSecretKey", "param", "param2", "token"],
    "identity_fields": ["kcid", "skbj"],
    "note": "double slash is intentional - matches the captured HAR",
}
SEARCH_TERMS = ("xqerSign", "encrptSecretKey", "param2", "kcid", "skbj",
                "wapController", "sign", "JSONObject", "HashMap")
SOURCE_SUFFIXES = (".smali", ".java", ".kt")
URL_RE = re.compile(r"https?://\S+")
LONG_LITERAL_RE = re.compile(r'"([^"]{40,})"')


@dataclass
class Hit:
    file: str
    line: int
    kind: str
    class_name: str
    method: str
    term: str
    snippet: str

    def public(self) -> dict:
        return asdict(self)


def _redact(line: str, limit: int = 120) -> str:
    s = URL_RE.sub("<url>", line.strip())
    s = LONG_LITERAL_RE.sub('"<long>"', s)
    return s[:limit]


def _scan_file(path: Path, root: Path,
               terms: Sequence[str]) -> List[Hit]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    kind = path.suffix.lstrip(".")
    cls, method = "", ""
    out: List[Hit] = []
    for n, line in enumerate(lines, 1):
        cm = re.search(r"^\s*\.class\s+.*?(L[^;\s]+);", line)
        if cm:
            cls = cm.group(1)
        mm = re.search(r"^\s*\.method\s+.*?\s([^\s(]+)\(", line)
        if mm:
            method = mm.group(1)
        jm = re.search(r"\b(class|interface)\s+([A-Za-z_$][\w$]*)", line)
        if jm and kind in ("java", "kt"):
            cls = jm.group(2)
        for term in terms:
            if term.lower() in line.lower():
                out.append(Hit(str(path.relative_to(root)), n, kind,
                               cls, method, term, _redact(line)))
                break
    return out


def locate_constructors(root: str,
                        terms: Sequence[str] = SEARCH_TERMS) -> List[Hit]:
    """Locate candidate signing / parameter-construction points in jadx output."""
    base = Path(root)
    if not base.exists():
        return []
    hits: List[Hit] = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES:
            hits.extend(_scan_file(p, base, terms))
    return hits


def rank(hits: Sequence[Hit]) -> Dict[str, Any]:
    """Score files by how many distinct signing terms they contain."""
    files: Dict[str, set] = {}
    for h in hits:
        files.setdefault(h.file, set()).add(h.term)
    scored = sorted(((len(v), k, sorted(v)) for k, v in files.items()),
                    reverse=True)
    return {"candidate_files": [
        {"file": k, "distinct_terms": n, "terms": t} for n, k, t in scored[:20]],
        "total_hits": len(hits),
        "best": scored[0][1] if scored else "",
        "judgement": "constructor point is where kcid/skbj are put into the "
                     "param map before the sign call; verify with a FRIDA stack"}


FRIDA_TEMPLATE = r"""// ops/signature.py generated hook - edit identity fields BEFORE signing.
// Never modify xqerSign / encrptSecretKey / param2 / token at the exit.
// Targets are placeholders; confirm the class/method with jadx + a stack trace.
Java.perform(function () {
    var TARGET_KCID = "__KCID__";
    var TARGET_SKBJ = "__SKBJ__";
    var changed = { kcid: 0, skbj: 0 };

    function patch(key, value) {
        if (key === "kcid") { changed.kcid++; return TARGET_KCID; }
        if (key === "skbj") { changed.skbj++; return TARGET_SKBJ; }
        return value;
    }

    // Path A: generic container hook. Works when the app builds a Map/JSONObject.
    try {
        var HM = Java.use("java.util.HashMap");
        HM.put.implementation = function (k, v) {
            return this.put(k, patch(String(k), v));
        };
    } catch (e) { console.log("[ops] HashMap hook skipped: " + e); }
    try {
        var JO = Java.use("org.json.JSONObject");
        JO.put.overload("java.lang.String", "java.lang.Object")
          .implementation = function (k, v) {
            return this.put(k, patch(String(k), v));
        };
    } catch (e) { console.log("[ops] JSONObject hook skipped: " + e); }

    // Path B: precise hook once jadx identifies the constructor.
    // Java.use("__CLASS__").__METHOD__.implementation = function (...) { ... };

    // Read-only observation of the signed request; do NOT alter signed fields.
    try {
        var C = Java.use("__CLASS__");
        C.__METHOD__.implementation = function () {
            console.log("[ops] constructor entered, changed=" +
                        JSON.stringify(changed));
            return this.__METHOD__.apply(this, arguments);
        };
    } catch (e) {
        console.log("[ops] precise hook pending jadx target: " + e);
    }
});
"""


def build_hook(kcid: str, skbj: str, class_name: str = "TARGET_CLASS",
               method_name: str = "TARGET_METHOD") -> str:
    """Return a Frida script. Static output only; nothing is executed."""
    return (FRIDA_TEMPLATE
            .replace("__KCID__", kcid.replace("\\", "\\\\").replace('"', '\\"'))
            .replace("__SKBJ__", skbj.replace("\\", "\\\\").replace('"', '\\"'))
            .replace("__CLASS__", class_name)
            .replace("__METHOD__", method_name))


def policy() -> dict:
    return {
        "principle": "let the app sign; change identity fields at construction",
        "why_not_exit_rewrite": "xqerSign covers param/param2/token; editing "
                                "the outgoing body invalidates the signature",
        "constructor_evidence": ["stack trace showing kcid/skbj put()",
                                 "sign call after the map is built",
                                 "response errcode changes when kcid is altered"],
        "never_touch": list(SIGN_REQUEST["signed_fields"]),
        "change_only": list(SIGN_REQUEST["identity_fields"]),
        "dead_end": "signature bound to timestamp + device fingerprint and "
                    "server-side attestation; then no parameter edit helps",
        "verification": ["test round only", "compare official readback via "
                         "ops/crosscheck.py", "never judge by response text"],
        "guards": {"no_production_replay": True,
                   "no_algorithm_reimplementation": True,
                   "no_write_requests_from_this_module": True,
                   "single_physical_submit": True},
        "placeholders": ["TARGET_APK", "TARGET_CLASS", "TARGET_METHOD",
                         "TARGET_KCID", "TARGET_SKBJ"],
    }


def demo() -> dict:
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ops-sig-") as td:
        root = Path(td)
        (root / "com").mkdir()
        (root / "com" / "Signer.smali").write_text(
            ".class public Lcom/xq/Signer;\n"
            ".method public static sign(Ljava/util/Map;)Ljava/lang/String;\n"
            "    const-string v0, \"xqerSign\"\n"
            "    const-string v1, \"encrptSecretKey\"\n"
            "    return-object v0\n"
            ".end method\n", encoding="utf-8")
        (root / "com" / "CourseParam.java").write_text(
            "package com.xq;\n"
            "class CourseParam {\n"
            "  void build() { JSONObject o = new JSONObject();\n"
            "    o.put(\"kcid\", \"C1\"); o.put(\"skbj\", \"01\");\n"
            "    o.put(\"param2\", \"x\"); }\n}\n", encoding="utf-8")
        hits = [h.public() for h in locate_constructors(str(root))]
        ranked = rank(locate_constructors(str(root)))
    hook = build_hook("TARGET_COURSE_ID", "TARGET_CLASS_ID")
    return {"request": SIGN_REQUEST, "hits": hits, "ranked": ranked,
            "hook_preview": hook.splitlines()[:14],
            "hook_len": len(hook),
            "hook_mentions_signed_field_edit": "xqerSign =" in hook,
            "policy": policy()}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--root", default="", help="jadx/smali output directory")
    ap.add_argument("--kcid", default="TARGET_COURSE_ID")
    ap.add_argument("--skbj", default="TARGET_CLASS_ID")
    ap.add_argument("--class", dest="cls", default="TARGET_CLASS")
    ap.add_argument("--method", default="TARGET_METHOD")
    ap.add_argument("--out", default="", help="write the Frida hook to this file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.demo:
        report = demo()
    else:
        hits = locate_constructors(args.root) if args.root else []
        report = {"request": SIGN_REQUEST,
                  "hits": [h.public() for h in hits],
                  "ranked": rank(hits), "policy": policy()}
    hook = build_hook(args.kcid, args.skbj, args.cls, args.method)
    report["hook_len"] = len(hook)
    if args.out:
        Path(args.out).write_text(hook, encoding="utf-8")
        report["hook_written"] = args.out
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("request=", SIGN_REQUEST["host"] + SIGN_REQUEST["path"])
        print("hits=", len(report["hits"]),
              "best=", report["ranked"]["best"])
        print("change_only=", SIGN_REQUEST["identity_fields"],
              "never_touch=", SIGN_REQUEST["signed_fields"])
        if args.out:
            print("hook written:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

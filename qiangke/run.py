#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""华水抢课（青果 KINGOSOFT）—— 命令行入口。

  python run.py probe                 探测入口
  python run.py login                 登录并保存会话（后续命令自动复用，不再打密码）
  python run.py login --fresh         忽略已存会话，强制重新登录并覆盖
  python run.py discover              登录后列出全部 .action 入口（找选课地址）
  python run.py import-curl --file curl.txt [--apply]   把真实选课请求导成模板
  python run.py import-cookies --file x.har --verify    从浏览器抓包恢复会话（不再打密码）
  python run.py check                 登录 + 校验 grab_request 是否配好
  python run.py go                    开抢
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from qk import log as qlog                  # noqa: E402
from qk.adapters import make                 # noqa: E402
from qk.captcha import CaptchaSolver         # noqa: E402
from qk.engine import Engine                 # noqa: E402
import qk.curl2req as curl2req               # noqa: E402
import qk.har_cookies as har_cookies         # noqa: E402
import qk.probe as probe_mod                 # noqa: E402


_FORCE_FRESH = False


def load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    sysc = cfg.setdefault("system", {})
    sysc.setdefault("log_dir", cfg.get("log_dir", "logs"))
    if _FORCE_FRESH:
        sysc["session_fresh"] = True
    return cfg


def save(cfg: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _adapter(cfg: dict, log):
    opts = cfg.get("system") or {}
    solver = CaptchaSolver(opts.get("captcha"), log, img_dir=cfg.get("log_dir", "logs"))
    acct = (cfg.get("accounts") or [{}])[0]
    courses = acct.get("courses") or cfg.get("courses") or []
    return make(opts.get("type") or "kingo", dict(acct, courses=courses), opts, log,
                solver=solver)


def cmd_probe(args, log) -> int:
    rep = probe_mod.run(log, [args.url] if args.url else None)
    with open("probe-result.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    log.info("探测详情 -> probe-result.json")
    if rep["pick"] and args.write:
        probe_mod.write_config(rep, args.config)
        log.ok(f"已回写 {args.config}：type={rep['pick']['system']} "
               f"base_url={rep['pick']['base_url']}")
    return 0 if rep["pick"] else 1


def cmd_login(args, log) -> int:
    cfg = load(args.config)
    ad = _adapter(cfg, log)
    if args.dry_run:
        # 不发请求，只打印将要发送的明文参数结构，供人工核对
        from qk.adapters.kingo import build_logon_body
        acct = (cfg.get("accounts") or [{}])[0]
        pwd = acct.get("pass", "")
        log.info(f"账号={acct.get('user')}  密码长度={len(pwd)}")
        bad = [c for c in pwd if c in "&=+ %?#"]
        if bad:
            log.error(f"密码含特殊字符 {bad} —— 表单拼接会破坏参数，先改密码再来")
        else:
            log.info("密码无特殊字符，可安全拼接")
        body = build_logon_body(acct.get("user", ""), pwd, "SESSION", "SESSIONS",
                                "12345678901234567890123", "2025-01-01 00:00:00", "", False)
        log.info("body 预览（前 80 字符）：" + body[:80] + " ...")
        return 0
    ad.open()
    if not ad.login():
        return 4 if getattr(ad, "blocked", False) else 2
    home = ad.p("home") if hasattr(ad, "p") else "/"
    r = ad.session.get(home)
    log.ok(f"登录成功，主页 {r.status}，长度 {len(r.text)}，"
           f"含失效提示={'凭证已失效' in r.text}")
    log.info(f"cookie: {list(ad.session.dump_cookies().keys())}")
    log.info(f"会话文件: {getattr(ad, 'cookie_file', '') or '未启用'}"
             f"（下次跑自动复用，--fresh 可强制重登）")
    return 0


def cmd_import_cookies(args, log) -> int:
    """从浏览器 HAR 恢复会话；--verify 只探测主页，绝不发登录请求。"""
    from qk.adapters.kingo import session_alive
    from qk.net import Session

    cfg = load(args.config)
    if not args.file or not os.path.isfile(args.file):
        log.error("需要 --file 指向一份已登录状态下的 HAR 抓包")
        return 2
    acct = (cfg.get("accounts") or [{}])[0]
    user = args.user or str(acct.get("user") or "")
    tpl = (cfg.get("system") or {}).get("session_file") or "logs/session-{user}.cookies"
    out = tpl.replace("{user}", user).replace("{name}", str(acct.get("name") or ""))
    if "{user}" not in tpl and user not in os.path.basename(out):
        root, ext = os.path.splitext(out)
        out = f"{root}-{user}{ext or '.cookies'}"

    if har_cookies.is_netscape(args.file):
        cookies, kind = har_cookies.extract_netscape(args.file), "cookies.txt"
    else:
        cookies, kind = har_cookies.extract(args.file), "HAR"
    if not cookies:
        log.error("文件里没找到 jwmis / authserver 域的 cookie")
        return 3
    n = har_cookies.write_jar(cookies, out)
    log.ok(f"已从 {kind} 恢复 {n} 条 cookie → {out}")
    for line in har_cookies.describe(cookies):
        log.info("  " + line)

    if args.verify:
        sysc = cfg.get("system") or {}
        s = Session(base_url=sysc.get("base_url") or "https://jwmis.ncwu.edu.cn",
                    timeout=float(sysc.get("timeout", 8)),
                    verify_tls=bool(sysc.get("verify_tls", True)),
                    logger=log, cookie_file=out)
        try:
            r = s.get("/hsjw/frame/homes.action", referer="/hsjw/", timeout=8)
        except Exception as e:
            log.warning(f"会话探测网络异常（不消耗登录次数）：{e}")
            return 0
        if session_alive(r.text, r.url, r.status):
            log.ok("会话仍有效 → 现在直接跑 window / list / go，0 次密码请求")
        else:
            log.warning("会话已过期 → 重新登录一次，或重新导出浏览器已登录状态的 HAR")
    return 0


def cmd_discover(args, log) -> int:
    cfg = load(args.config)
    ad = _adapter(cfg, log)
    ad.open()
    if not ad.login():
        log.error("登录失败，无法枚举菜单")
        return 2
    acts = ad.discover()
    log.ok(f"共 {len(acts)} 个入口：")
    for a in acts:
        mark = "  <== 可能是选课" if any(k in a.lower() for k in ("xk", "xuan", "select")) else ""
        print(f"  {a}{mark}")
    return 0


def cmd_import_curl(args, log) -> int:
    raw = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
    req = curl2req.parse(raw)
    tpl, marks = curl2req.to_template(req)
    log.info(f"解析：{curl2req.summarize(req)}")
    if marks:
        log.info("识别到课程标识字段：" + ", ".join(marks))
    else:
        log.warning("没识别到课程标识字段，模板里需手工把值改成 {xkid} 之类占位符")
    print(json.dumps(tpl, ensure_ascii=False, indent=2))
    if args.apply:
        cfg = load(args.config)
        cfg.setdefault("system", {})["grab_request"] = tpl
        save(cfg, args.config)
        log.ok(f"已写入 {args.config} -> system.grab_request")
    return 0


def cmd_diff_login(args, log) -> int:
    """把浏览器真实登录请求解开，与本地生成的参数逐字段比对（不登录、不消耗次数）。"""
    import qk.diag as diag
    cfg = load(args.config)
    acct = (cfg.get("accounts") or [{}])[0]
    raw = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
    rep = diag.diff(raw, acct.get("user", ""), acct.get("pass", ""))
    print(diag.render(rep))
    return 0 if rep.get("ok") else 1


def cmd_auto(args, log) -> int:
    """逐账号登录 → 拉选课列表 → 按课程名自动匹配 xkid → 回写配置。"""
    cfg = load(args.config)
    opts = cfg.get("system") or {}
    solver = CaptchaSolver(opts.get("captcha"), log, img_dir=cfg.get("log_dir", "logs"))
    accounts = cfg.get("accounts") or []
    if not accounts:
        log.error("配置里没有 accounts")
        return 3

    total, hit = 0, 0
    for idx, acct in enumerate(accounts):
        courses = acct.get("courses") or []
        ad = make(opts.get("type") or "kingo", dict(acct, courses=courses), opts, log,
                  solver=solver)
        ad.open()
        if not ad.login():
            if getattr(ad, "blocked", False):
                log.error(f"账号 {acct.get('name')} 凭据熔断，跳过全部剩余账号")
                break
            log.warning(f"账号 {acct.get('name')} 登录失败，跳过")
            continue
        ad.prepare(courses)
        for c in courses:
            total += 1
            xk = c.get("xkid") or ""
            hit += 1 if xk else 0
            flag = "OK " if xk else "-- "
            print(f"  {flag}{acct.get('name',''):<6}{str(c.get('name'))[:16]:<18}"
                  f"{str(c.get('teacher') or '')[:8]:<10}xkid={xk or '(未解析)'}")

    print(f"\n解析成功 {hit}/{total}")
    if args.apply and hit:
        for idx, acct in enumerate(accounts):
            src = acct.get("courses") or []
            for j, c in enumerate(src):
                if c.get("xkid"):
                    cfg["accounts"][idx]["courses"][j]["xkid"] = c["xkid"]
        save(cfg, args.config)
        log.ok(f"已回写 {args.config}（下次可直接 go）")
    elif hit == 0:
        log.warning("一条都没匹配上 —— 把日志发我，或手工填 xkid / 用 import-curl")
    return 0 if hit and hit == total else 1


def cmd_list(args, log) -> int:
    """列出当前各选课流程下的可选课程（青果 DataTable 模式）。"""
    from qk import kingo_xk as X

    cfg = load(args.config)
    ad = _adapter(cfg, log)
    ad.open()
    if not ad.login():
        log.error("登录失败")
        return 2
    if not hasattr(ad, "list_courses"):
        log.error("当前适配器不支持")
        return 1

    kinds = [args.kind] if getattr(args, "kind", "") else \
        ["pre_elect", "by_plan", "by_volunteer", "retake"]
    total_rows = 0
    for kind in kinds:
        try:
            info = ad.list_courses(kind)
        except Exception as e:                                   # noqa: BLE001
            log.warning(f"{kind}: 拉取失败 {e}")
            continue
        win = info.get("window") or {}
        flag = "★开放" if win.get("open") else ("已过期" if win.get("qssj") else "未定义")
        rows = info.get("rows") or []
        total_rows += len(rows)
        print()
        log.info(f"[{kind}] {win.get('lcmc') or '-'}  {flag}  "
                 f"{win.get('qssj') or '-'} ~ {win.get('jssj') or '-'}")
        log.info(f"         tableId={info.get('table_id')}  总记录={info.get('total')}")
        if not rows:
            log.warning("         本轮无可选课程")
            continue
        print(f"         {'课程':<16}{'班号':<8}{'班级名':<12}{'教师':<10}{'学分':<6}{'状态'}")
        for r in rows:
            log.info(f"         {X.course_name(r):<16}{r.get('skbh',''):<8}"
                     f"{r.get('skbjmc',''):<12}{r.get('rkjs',''):<10}"
                     f"{r.get('xf',''):<6}{r.get('xk_status','')}")
    return 0 if total_rows else 1


def cmd_window(args, log) -> int:
    """遍历所有选课流程，列出哪些正在开放。只查单一 xktype 会漏窗口。"""
    cfg = load(args.config)
    ad = _adapter(cfg, log)
    ad.open()
    if not ad.login():
        log.error("登录失败")
        return 2
    if not hasattr(ad, "check_all_windows"):
        log.error("当前适配器不支持时间窗检查")
        return 1

    one = int(getattr(args, "xktype", 0) or 0)
    infos = ([ad.check_xk_window(one)] if one else ad.check_all_windows())
    if one:
        infos[0]["xktype"] = one

    print()
    opened = []
    for info in infos:
        t = info.get("xktype")
        name = info.get("lcmc") or (info.get("message") or "-")
        qssj, jssj = info.get("qssj") or "-", info.get("jssj") or "-"
        if info.get("open"):
            opened.append(info)
            log.ok(f"[{t}] {name}   {qssj} ~ {jssj}   ★ 开放中")
        elif info.get("qssj"):
            log.info(f"[{t}] {name}   {qssj} ~ {jssj}   已过期/未开始")
        else:
            log.info(f"[{t}] {name}")

    print()
    if opened:
        for info in opened:
            log.ok(f"可抢：{info.get('lcmc')}（xktype={info.get('xktype')}，"
                   f"lcid={info.get('lcid')}）")
            log.info(f"     学期 {info.get('xnxqDesc')}  "
                     f"范围 {info.get('kcfwmc') or info.get('kcfw') or '-'}  "
                     f"年级 {info.get('nj')} 专业 {info.get('zymc')}")
        return 0
    log.warning("当前没有任何选课流程处于开放状态")
    return 1


def cmd_check(args, log) -> int:
    cfg = load(args.config)
    ad = _adapter(cfg, log)
    ad.open()
    if not ad.login():
        log.error("登录失败")
        return 2
    if hasattr(ad, "check_xk_window"):
        info = ad.check_xk_window()
        if info.get("open"):
            log.ok(f"选课窗口开启中：{info.get('qssj')} ~ {info.get('jssj')}")
        else:
            log.warning(f"选课窗口未开放（{info.get('qssj') or '?'} ~ {info.get('jssj') or '?'}）")
    gr = getattr(ad, "grab_request", None)
    if gr:
        log.ok(f"grab_request 已配置：{gr.get('method')} {gr.get('url')}")
        log.info("字段：" + json.dumps(gr.get("form") or gr.get("json") or {},
                                      ensure_ascii=False)[:200])
    else:
        log.warning("grab_request 为空 —— 先跑 python run.py import-curl --file curl.txt --apply")
    log.info("验证码模式：" + str((cfg.get("system") or {}).get("captcha", {}).get("mode")))
    return 0 if gr else 1


def cmd_go(args, log) -> int:
    cfg = load(args.config)
    if args.dry:
        cfg.setdefault("policy", {})["max_seconds"] = 1
    res = Engine(cfg, log).run()
    ok = [r for r in res if r["code"] in ("success", "enrolled")]
    seen, rows = set(), []
    for r in res:
        k = (r["account"], r["course"])
        if k in seen:
            continue
        seen.add(k)
        rows.append(r)
    print()
    print(f"{'账号':<8}{'课程':<18}{'结果':<10}{'次数':<6}说明")
    print("-" * 80)
    for r in rows:
        print(f"{str(r['account'])[:6]:<8}{str(r['course'])[:16]:<18}{r['code']:<10}"
              f"{r['attempts']:<6}{str(r['msg'])[:30]}")
    print(f"\n成功 {len(ok)} / 目标 {len(rows)}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="华北水利水电大学 抢课工具（青果 KINGOSOFT）")
    ap.add_argument("cmd", nargs="?", default="go",
                    choices=["go", "auto", "probe", "login", "discover", "import-curl",
                             "import-cookies", "check", "diff-login", "window", "list"])
    ap.add_argument("-c", "--config", default="config.ncwu.json")
    ap.add_argument("--url", default="", help="probe 指定地址")
    ap.add_argument("--file", default="", help="import-curl / import-cookies 读文件")
    ap.add_argument("--apply", action="store_true", help="import-curl 结果写入配置")
    ap.add_argument("--write", action="store_true", help="probe 结果写入配置")
    ap.add_argument("--dry", action="store_true", help="只跑 1 秒验证链路")
    ap.add_argument("--kind", default="",
                    help="选课页类型：pre_elect / by_plan / by_volunteer / retake")
    ap.add_argument("--dry-run", action="store_true",
                    help="login 时只做本地自检，不发登录请求（不消耗错误次数）")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--fresh", action="store_true",
                    help="忽略已保存的会话，强制重新登录（登录后覆盖会话文件）")
    ap.add_argument("--user", default="", help="import-cookies 指定学号，决定会话文件名")
    ap.add_argument("--verify", action="store_true",
                    help="import-cookies 后探测主页是否有效（不发登录请求）")
    args = ap.parse_args()

    global _FORCE_FRESH
    _FORCE_FRESH = args.fresh
    os.chdir(HERE)
    log = qlog.build("qk", os.path.join(HERE, "logs"), quiet=args.quiet)
    log.info(f"华水抢课 v{__import__('qk').__version__}  cmd={args.cmd}  cfg={args.config}")
    table = {"probe": cmd_probe, "login": cmd_login, "discover": cmd_discover,
             "import-curl": cmd_import_curl, "import-cookies": cmd_import_cookies,
             "check": cmd_check, "go": cmd_go,
             "auto": cmd_auto, "diff-login": cmd_diff_login, "window": cmd_window, "list": cmd_list}
    try:
        return table[args.cmd](args, log)
    except KeyboardInterrupt:
        log.warning("用户中断")
        return 130
    except FileNotFoundError as e:
        log.error(f"文件不存在: {e.filename}")
        return 3


if __name__ == "__main__":
    sys.exit(main())

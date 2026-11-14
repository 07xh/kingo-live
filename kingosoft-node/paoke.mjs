#!/usr/bin/env node
/**
 * paoke.mjs — 华北水利水电大学 强智教务 选课辅助工具 v3
 *
 * 接口全部来自真实流量（浏览器 HAR），无一处猜测。
 *
 * 完整链：
 *   ① 初始化   POST /jw/common/getWsxkTimeRange.action?xktype=2
 *                → xh / xn / xqM / lcid / qssj / jssj（选课窗口起止）
 *   ② 课程表   GET  /student/wsxk.zx.html?menucode=S2020202  → 取 tableId
 *              POST /taglib/DataTable.jsp?tableId=<tid>       → 课程行（含 skbjdm、余量）
 *   ③ 选课弹窗 GET  /student/report/wsxk.zx_promt.jsp?params=enc(...)&token&timestamp
 *                → ActionForm 全量真实值 + 上课班级 tableId
 *   ④ 班级表   /taglib/DataTable.jsp?tableId=<tid2>&fre=1&xn&xq_m&xh&kcdm&...
 *   ⑤ 提交     POST /jw/common/saveElectiveCourse.action
 *                body = params=base64(DES(serializedForm)) & token & timestamp
 *   ⑥ 退选     POST /jw/common/cancelElectiveCourse.action    (xktype=5)
 *
 * 用法:
 *   node paoke.mjs import <xxx.har>        从浏览器 HAR 导入 cookie，免密码
 *   node paoke.mjs init                    拉取学号/学年/学期/lcid/选课窗口
 *   node paoke.mjs windows                 七个轮次的开放状态与起止时间
 *   node paoke.mjs time                    当前轮次窗口 + 倒计时 + 时钟偏差
 *   node paoke.mjs calib                   只算时钟偏差（毫秒）
 *   node paoke.mjs list [类别码]            列可选课程 + 余量 + skbjdm
 *   node paoke.mjs skb <kcdm>              列该课程全部上课班级 + 余量
 *   node paoke.mjs grab <kcdm|skbjdm>      抢课（--skb= 指定班，--conc= 并发，--dry 只打印）
 *   node paoke.mjs watch <kcdm> [秒]        轮询余量，见空位立刻抢
 *   node paoke.mjs my                      已选课程
 *   node paoke.mjs drop <kcdm> <skbjdm> <skbzdm>   退选
 *   node paoke.mjs raw <path> <明文参数>    任意接口直发（调试）
 */
import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import vm from 'vm';
import { fileURLToPath } from 'url';
import { nodeScopeFromWindow, reserveNodeSharedIntent } from '../contracts/node_shared_intent.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'https://jwmis.ncwu.edu.cn/hsjw';
const SESS_FILE = path.join(__dirname, 'session.json');
const OUT_DIR = path.join(__dirname, 'paoke_out');

const GBK_SUBMIT = '%CC%E1%BD%BB'; // "提交"
const MENUCODE = 'S2020202';
const SUBMIT_PATH = '/jw/common/saveElectiveCourse.action';
const SUBMIT_AUTHORITY = Symbol('durable-intent-reserved');       // 网上选课 → 正选

const md5 = (s) => crypto.createHash('md5').update(s, 'utf8').digest('hex');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => Date.now();

// ============ DES ============
let strEnc = null;
function desReady() {
  if (strEnc) return strEnc;
  const src = fs.readFileSync(path.join(__dirname, 'jkingo.des.js'), 'utf8');
  const ctx = vm.createContext({});
  vm.runInContext(src + '\n;globalThis.__strEnc = strEnc;', ctx);
  strEnc = ctx.__strEnc;
  return strEnc;
}

// 强智混用编码：.action/.do 多为 UTF-8，.jsp/.html 为 GBK
function decode(buf) {
  const b = Buffer.from(buf);
  try { return new TextDecoder('utf-8', { fatal: true }).decode(b); } catch { /* fallthrough */ }
  try { return new TextDecoder('gbk').decode(b); } catch { return b.toString('utf8'); }
}
const stripTags = (s) =>
  String(s).replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();
// jQuery.param 语义：encodeURIComponent 后把 %20 换成 +
const jqEnc = (v) => encodeURIComponent(String(v)).replace(/%20/g, '+');

// ============ 会话 ============
function loadSess() {
  if (!fs.existsSync(SESS_FILE)) {
    console.error('没有会话。先跑:  node paoke.mjs import <xxx.har>');
    process.exit(1);
  }
  return JSON.parse(fs.readFileSync(SESS_FILE, 'utf8'));
}
const saveSess = (s) => fs.writeFileSync(SESS_FILE, JSON.stringify(s, null, 2));

// ============ HTTP ============
function hdrs(sess, referer, ajax = true) {
  const h = {
    'User-Agent':
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0',
    Accept: ajax ? '*/*' : 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    Cookie: sess.cookie,
    Referer: referer || BASE + '/frame/homes.action',
  };
  if (ajax) h['X-Requested-With'] = 'XMLHttpRequest';
  return h;
}

async function get(p, sess, referer) {
  const r = await fetch(BASE + p, { headers: hdrs(sess, referer, false) });
  const b = Buffer.from(await r.arrayBuffer());
  return { status: r.status, text: decode(b), raw: b };
}

/**
 * 强智加密参数。DES 密钥一次性，每次请求都必须重新取。
 *   token  = md5( md5(plain) + md5(timestamp) )
 *   params = base64( strEnc(plain, tempDeskey, null, null) )
 * 返回可直接作为 body 的字符串。
 */
async function seal(plain, sess) {
  const enc = desReady();
  const [dk, ts] = await Promise.all([
    get('/frame/homepage?method=getTempDeskey', sess).then((r) => r.text.trim()),
    get('/frame/homepage?method=getTempNowtime', sess).then((r) => r.text.trim()),
  ]);
  if (!dk || dk.length < 8) throw new Error('取 DES key 失败: ' + JSON.stringify(dk.slice(0, 80)));
  const token = md5(md5(plain) + md5(ts));
  const p = Buffer.from(enc(plain, dk, null, null), 'utf8').toString('base64');
  return `params=${encodeURIComponent(p)}&token=${token}&timestamp=${encodeURIComponent(ts)}`;
}

async function post(p, body, sess, referer, permit = null) {
  // Raw diagnostics and probes cannot bypass the shared durable intent fence.
  if (String(p).split('?')[0] === SUBMIT_PATH) {
    if (!permit || permit.authority !== SUBMIT_AUTHORITY || permit.spent !== false) {
      throw new Error('提交被阻止：缺少共享 SQLite 持久化意图');
    }
    permit.spent = true; // consume before the first possible physical send
  }
  const r = await fetch(BASE + p, {
    method: 'POST',
    headers: { ...hdrs(sess, referer, true), 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body,
  });
  const b = Buffer.from(await r.arrayBuffer());
  return { status: r.status, text: decode(b), raw: b };
}

function parseJSON(t) {
  try { return JSON.parse(t); } catch { /* BOM / 空白 */ }
  try { return JSON.parse(t.replace(/^\uFEFF/, '').trim()); } catch { return null; }
}

// ============ 解析 ============
/** 把 DataTable 的 <tr id='trN'> 解析成 [{单元格 name: 文本}] */
function parseRows(html) {
  const body = (html.match(/id=["']sdTable_tbody["'][^>]*>([\s\S]*?)<\/tbody>/) || [, ''])[1];
  const trs = body.match(/<tr[^>]*>[\s\S]*?<\/tr>/g) || [];
  const out = [];
  for (const tr of trs) {
    const cells = [...tr.matchAll(/<td[^>]*\bname=['"]([^'"]+)['"][^>]*>([\s\S]*?)<\/td>/g)];
    if (!cells.length) continue;
    const row = {};
    for (const c of cells) row[c[1]] = stripTags(c[2]);
    if (Object.values(row).some((v) => v)) out.push(row);
  }
  return out;
}

/** 解析页面内 ActionForm 的 input，保持 DOM 顺序 */
function parseForm(html) {
  const i = html.search(/id=["']ActionForm["']/);
  const seg = i >= 0 ? html.slice(i) : html;
  const end = seg.search(/<\/form>/);
  const scope = end > 0 ? seg.slice(0, end) : seg;
  const fields = [];
  for (const m of scope.matchAll(/<input\b[^>]*>/gi)) {
    const tag = m[0];
    const name = (tag.match(/\bname=["']([^"']*)["']/i) || [, ''])[1];
    const type = ((tag.match(/\btype=["']([^"']*)["']/i) || [, 'text'])[1] || 'text').toLowerCase();
    const value = (tag.match(/\bvalue=["']([^"']*)["']/i) || [, ''])[1];
    const checked = /\bchecked\b/i.test(tag);
    if (!name) continue;
    if (type === 'radio' || type === 'checkbox') { if (checked) fields.push([name, value]); continue; }
    if (type === 'button' || type === 'submit' || type === 'reset') continue;
    fields.push([name, value]);
  }
  return fields;
}

/** 表单序列化：去 FormBean 前缀 → URL 编码 → & 连接（等价 jQuery.serialize） */
function serialize(fields) {
  return fields
    .map(([n, v]) => `${jqEnc(n.replace(/^electiveCourseForm\./, ''))}=${jqEnc(v)}`)
    .join('&');
}
const setField = (fields, name, value, append = false) => {
  const hit = fields.find(([n]) => n === name || n === 'electiveCourseForm.' + name);
  if (hit) hit[1] = String(value);
  else if (append) fields.push([name, String(value)]);
};

// ============ ① 初始化 ============
async function initSession(sess) {
  const r = await post('/jw/common/getWsxkTimeRange.action?xktype=2', '', sess,
    BASE + '/student/wsxk.zx.html?menucode=' + MENUCODE);
  const j = parseJSON(r.text);
  if (!j) throw new Error('getWsxkTimeRange 返回非 JSON: ' + r.text.slice(0, 200));
  // status=400 表示"当前不在有效选课时间段"，但 result 里照样带 xh/xn/xq/lcid/窗口起止，必须照收
  let res = j.result;
  if (typeof res === 'string') { try { res = JSON.parse(res); } catch { /* 已经是对象 */ } }
  if (!res || typeof res !== 'object') throw new Error('无法解析 result: ' + r.text.slice(0, 300));

  Object.assign(sess, {
    status: String(j.status),
    message: j.message || '',
    xh: res.xh ?? sess.xh,
    xn: res.xn ?? sess.xn,
    xq: res.xqM ?? sess.xq,
    lcid: res.lcid ?? sess.lcid,
    nj: res.nj ?? sess.nj,
    zydm: res.zydm ?? sess.zydm,
    qssj: res.qssj ?? sess.qssj,
    jssj: res.jssj ?? sess.jssj,
    xfsx: res.xfsx ?? sess.xfsx,
    mssx: res.mssx ?? sess.mssx,
    xnxqDesc: res.xnxqDesc ?? sess.xnxqDesc,
  });
  saveSess(sess);
  return res;
}

// ============ ② 课程列表 ============
async function fetchCourseList(sess, kclb1 = '', kclb2 = '') {
  const page = await get('/student/wsxk.zx.html?menucode=' + MENUCODE, sess);
  if (!page.text.includes('ActionForm')) throw new Error('正选页异常（会话失效？）: ' + page.text.slice(0, 120));
  const tid = (page.text.match(/DataTable\.jsp\?tableId=(\d+)/) || [, ''])[1];
  if (!tid) throw new Error('未从正选页取到 tableId');

  const body = [
    'initQry=1', 'xktype=2',
    `xn=${sess.xn}`, `xq=${sess.xq}`, `nj=${sess.nj || ''}`,
    'pycc=05', 'dwh=05', `zydm=${sess.zydm || ''}`,
    `kclb1=${kclb1}`, `kclb2=${kclb2}`, 'isbyk=', 'items=',
    `xnxq=${sess.xn}%2C${sess.xq}`,
    `btnSubmit=${GBK_SUBMIT}`,
    'sel_kclb1=', 'sel_kc=', 'sel_rkjs=',
    'menucode_current=' + MENUCODE,
  ].join('&');

  const r = await post(`/taglib/DataTable.jsp?tableId=${tid}&clientWidth=1210`, body, sess,
    BASE + '/student/wsxk.zx.html?menucode=' + MENUCODE);
  return { tid, html: r.text, rows: parseRows(r.text) };
}

// ============ ③ 选课弹窗 ============
async function fetchPromt(sess, kcdm, xf = '') {
  const plain = [
    `kcdm=${kcdm}`, `xh=${sess.xh}`, `xn=${sess.xn}`, `xq=${sess.xq}`,
    'checked=false', 'skbjdm=', `nj=${sess.nj || ''}`, `zydm=${sess.zydm || ''}`,
    'xsbjdm=', 'kclb1=', 'kclb2=', 'khfs=', `xf=${xf || sess.xf || '0'}`,
    'xktype=2', `lcid=${sess.lcid}`, 'xxkckzfs=0', 'yxsjct=0', 'yxkzyfxxk=0',
    'isbuybook=', 'iscx=', 'isyxtj=', 'kcfw=zxbnj', 'kclb3=', 'xfsx=', 'mssx=',
  ].join('&');
  const body = await seal(plain, sess);
  const r = await get(`/student/report/wsxk.zx_promt.jsp?${body}`, sess,
    BASE + '/student/wsxk.zx.html?menucode=' + MENUCODE);
  if (!r.text.includes('ActionForm')) throw new Error('弹窗页异常: ' + r.text.slice(0, 200));
  return { html: r.text, fields: parseForm(r.text) };
}

/** ④ 上课班级列表 */
async function fetchSkbList(sess, kcdm, promtHtml) {
  const tid = (promtHtml.match(/DataTable\.jsp\?tableId=(\d+)/) || [, ''])[1];
  if (!tid) return { tid: '', rows: [] };
  const qs = [
    `xn=${sess.xn}`, `xq_m=${sess.xq}`, `xh=${sess.xh}`, `kcdm=${kcdm}`,
    'skbjdm=', 'xktype=2', 'kcfw=zxbnj', 'isyxkc=false', `lcid=${sess.lcid}`, 'xsbjdm=',
  ].join('&');
  const r = await get(`/taglib/DataTable.jsp?tableId=${tid}&fre=1&${qs}`, sess,
    BASE + '/student/report/wsxk.zx_promt.jsp');
  return { tid, rows: parseRows(r.text) };
}

/** 从 "已选/免听" 字段（如 123/0）解析已选人数，配合 xkrssx(限选) 算余量 */
function capacity(row) {
  const lim = Number((row.xkrssx || '').replace(/[^\d]/g, '')) || 0;
  const picked = Number(String(row.xkrs || '').split('/')[0].replace(/[^\d]/g, '')) || 0;
  return { lim, picked, left: lim ? lim - picked : 0 };
}

// ============ ⑤ 提交选课 ============
function buildElectPlain(fields, { skbjdm, skbzdm, outnumber, zfx_m }) {
  setField(fields, 'skbjdm', skbjdm || '');
  setField(fields, 'skbzdm', skbzdm || '');
  setField(fields, 'outnumber', outnumber ?? '0');
  setField(fields, 'zfx_m', zfx_m ?? '0');
  // 与页面 submit 逻辑对齐
  setField(fields, 'is_buy_book', '0');
  setField(fields, 'is_cx', '0');
  setField(fields, 'is_yxtj', '1');   // 允许上课班调剂，抢课命中率更高
  setField(fields, 'is_checkTime', '1'); // 不许时间冲突，避免选上后与已选课冲突
  setField(fields, 'xk_points', '0');
  return serialize(fields);
}

function verdict(text) {
  if (/请求太过频繁|访问频繁|稍后再试/.test(text)) return { code: 'THROTTLE', msg: '服务端频控，需退避' };
  const j = parseJSON(text) || {};
  const status = String(j.status || '');
  const msg = String(j.message || '');
  if (status === '200') return { code: 'OK', msg };
  if (/凭证|登录|失效|无效的访问请求/.test(msg) || status === '300') return { code: 'AUTH', msg };
  if (/不是选课学生本人/.test(msg)) return { code: 'NOTSTU', msg };
  if (/时间.*区段|非有效/.test(msg)) return { code: 'CLOSED', msg };
  if (/人数.*(满|上限)|已满|名额/.test(msg)) return { code: 'FULL', msg };
  if (/冲突/.test(msg)) return { code: 'CONFLICT', msg };
  if (/学分|门数|上限/.test(msg)) return { code: 'LIMIT', msg };
  if (/已选|重复/.test(msg)) return { code: 'DUP', msg };
  return { code: 'OTHER', msg, status };
}

async function submitElect(sess, plain, scope) {
  // Prepare request data before reserving the one physical write.
  const body = await seal(plain, sess);
  // Reuse Qiangke's existing SQLite salt, schema and historical fingerprints.
  if (!reserveNodeSharedIntent(scope)) {
    return { code: 'BLOCKED', msg: '该课程已有提交意图，禁止重复发送；请核对官方已选记录' };
  }
  const permit = { authority: SUBMIT_AUTHORITY, spent: false };
  try {
    const r = await post(SUBMIT_PATH, body, sess,
      BASE + '/student/report/wsxk.zx_promt.jsp', permit);
    // HTTP acknowledgement and business success text are NOT official proof.
    return { code: 'PENDING', msg: '请求结果需用官方已选课程明细核对',
      transport: verdict(r.text).code };
  } catch {
    return { code: 'PENDING', msg: '请求状态不确定，保留意图，禁止重发' };
  }
}

// ============ 命令: import ============
function cmdImport(harPath) {
  const har = JSON.parse(fs.readFileSync(harPath, 'utf8'));
  const entries = har.log?.entries || [];
  const jw = entries.filter((e) => /jwmis\.ncwu\.edu\.cn/.test(e.request.url));
  if (!jw.length) throw new Error('HAR 里没有 jwmis.ncwu.edu.cn 的请求');

  let cookie = '';
  for (const e of jw) {
    const c = e.request.headers.find((h) => h.name.toLowerCase() === 'cookie');
    if (c && /JSESSIONID/i.test(c.value)) { cookie = c.value.trim(); break; }
  }
  if (!cookie) throw new Error('HAR 里找不到 JSESSIONID');
  const sid = cookie.match(/JSESSIONID=([^;]+)/i);
  const sess = { cookie: sid ? `JSESSIONID=${sid[1]}` : cookie, ts: new Date().toISOString() };

  // 顺手把 HAR 里出现过的真实参数抓出来做兜底
  for (const e of jw) {
    const u = new URL(e.request.url);
    const q = new URLSearchParams(u.search);
    for (const k of ['xh', 'xn', 'xq', 'xq_m', 'nj', 'zydm', 'lcid']) {
      const v = q.get(k);
      if (v && !sess[k]) sess[k] = v;
    }
    const pd = e.request.postData?.text || '';
    const pq = new URLSearchParams(pd);
    for (const k of ['xh', 'xn', 'xq', 'nj', 'zydm', 'pycc', 'dwh']) {
      const v = pq.get(k);
      if (v && !sess[k]) sess[k] = v;
    }
    const m = pd.match(/lcid=(\d+)/);
    if (m) sess.lcid = m[1];
    if (!sess.tableId && /tableId=(\d+)/.test(u.search)) {
      sess.tableId = Number(u.search.match(/tableId=(\d+)/)[1]);
    }
  }
  if (sess.xq === '0' || sess.xq === undefined) sess.xq = sess.xq ?? '0';
  saveSess(sess);
  console.log('会话已导入:', SESS_FILE);
  console.table(sess);
  console.log('\n下一步: node paoke.mjs init');
}

// ============ 命令: init / time / calib ============
async function cmdInit(sess) {
  const res = await initSession(sess);
  console.log('初始化成功');
  for (const k of ['xh', 'xn', 'xqM', 'nj', 'zydm', 'lcid', 'qssj', 'jssj', 'xnxqDesc', 'xfsx', 'mssx']) {
    if (res[k] !== undefined && res[k] !== '') console.log(' ', k.padEnd(10), res[k]);
  }
  if (res.qssj) console.log('\n选课窗口:', res.qssj, '→', res.jssj);
}

async function clockOffset(sess) {
  const t0 = now();
  const r = await get('/frame/homepage?method=getTempNowtime', sess);
  const t1 = now();
  const srv = new Date(String(r.text).trim().replace(/-/g, '/')).getTime();
  if (!srv) return null;
  return { offset: srv - Math.round((t0 + t1) / 2), rtt: t1 - t0, srvText: String(r.text).trim() };
}

async function cmdCalib(sess) {
  const c = await clockOffset(sess);
  if (!c) return console.log('取服务器时间失败');
  console.log(`服务器时间: ${c.srvText}`);
  console.log(`往返时延  : ${c.rtt} ms`);
  console.log(`时钟偏差  : ${c.offset > 0 ? '+' : ''}${c.offset} ms  (${c.offset > 0 ? '本机慢，应提前' : '本机快，应延后'} ${Math.abs(c.offset)} ms)`);
}

async function cmdTime(sess) {
  if (!sess.qssj) await initSession(sess);
  const c = await clockOffset(sess);
  const parse = (s) => new Date(String(s).replace(/-/g, '/')).getTime();
  const qs = parse(sess.qssj), js = parse(sess.jssj);
  const srvNow = now() + (c ? c.offset : 0);
  const fmt = (ms) => {
    const d = Math.floor(Math.abs(ms) / 86400000), h = Math.floor((Math.abs(ms) % 86400000) / 3600000);
    const m = Math.floor((Math.abs(ms) % 3600000) / 60000), s = Math.floor((Math.abs(ms) % 60000) / 1000);
    return `${d}天${h}时${m}分${s}秒`;
  };
  console.log('学期     :', sess.xnxqDesc || `${sess.xn}-${sess.xq}`);
  console.log('开始时间 :', sess.qssj);
  console.log('结束时间 :', sess.jssj);
  console.log('时钟偏差 :', (c ? c.offset : 0) + ' ms');
  if (srvNow < qs) console.log('\n状态     : 未开始，距离开始', fmt(qs - srvNow));
  else if (srvNow > js) console.log('\n状态     : 已结束，超过', fmt(srvNow - js));
  else console.log('\n状态     : 进行中，剩余', fmt(js - srvNow));
}

// ============ 命令: list / skb ============
async function cmdList(sess, kclb1 = '') {
  const { tid, rows } = await fetchCourseList(sess, kclb1);
  if (!rows.length) return console.log('课程列表为空（未开放 / 会话失效 / 类别无课）');
  console.log(`tableId=${tid}  共 ${rows.length} 门\n`);
  const head = ['课号', '课程名', '学分', '教师', '限选', '已选', '余量', '上课班号(skbjdm)'];
  const body = rows.map((r) => {
    const kc = r.kc || '';
    const m = kc.match(/^\[([^\]]+)\](.*)$/);
    const { lim, picked, left } = capacity(r);
    return [
      m ? m[1] : '',
      m ? m[2] : kc,
      r.xf || '',
      (r.rkjs || '').trim(),
      lim || '',
      picked,
      lim ? (left > 0 ? String(left) : '满') : '',
      r.skbjdm || '',
    ];
  });
  const w = head.map((h, i) => Math.max(...[h, ...body.map((r) => String(r[i]))].map((s) => [...String(s)].length * 2)));
  const pad = (s, n) => String(s) + ' '.repeat(Math.max(0, n - [...String(s)].length * 2));
  console.log(head.map((h, i) => pad(h, w[i])).join(' '));
  console.log(w.map((n) => '─'.repeat(n)).join(' '));
  for (const r of body) console.log(r.map((c, i) => pad(c, w[i])).join(' '));
}

async function cmdSkb(sess, kcdm, xf) {
  const p = await fetchPromt(sess, kcdm, xf);
  const { tid, rows } = await fetchSkbList(sess, kcdm, p.html);
  console.log(`课程 ${kcdm}  弹窗页 ${p.html.length}B  班级表 tableId=${tid}  共 ${rows.length} 个上课班\n`);
  if (!rows.length) {
    console.log('该课程无可选上课班（可能已选满 / 已选过 / 不开放）');
    return;
  }
  for (const r of rows) {
    const { lim, picked, left } = capacity(r);
    console.log(`  skbjdm=${r.skbjdm || '(空)'}  ${r.skbjzmc || ''}  ${(r.rkjs || '').trim()}`);
    console.log(`    限选 ${lim || '-'}  已选 ${picked}  余量 ${lim ? left : '-'}   ${r.sksj || ''}  ${r.skdd || ''}`);
  }
}

// ============ 命令: grab ============
function resolveTarget(arg) {
  // 支持直接给 skbjdm（0400111101-002）或 kcdm
  const m = String(arg).match(/^(\d+)-(\w+)$/);
  return m ? { kcdm: m[1], skbjdm: arg } : { kcdm: String(arg), skbjdm: '' };
}

/**
 * 只做一次：拉弹窗页 → 组装明文参数 → 缓存。
 * 之后每次重试只走 seal()+POST，把单次开销从 4 个请求压到 3 个。
 */
async function prepareElect(sess, kcdm, skbjdm, xf) {
  const p = await fetchPromt(sess, kcdm, xf);
  const f = p.fields;
  if (!f.length) return { error: 'ActionForm 解析为空' };

  let skb = skbjdm, skbzdm = '', outnumber = '0', zfx = '0';
  if (!skb) {
    const { rows } = await fetchSkbList(sess, kcdm, p.html);
    if (!rows.length) return { error: `该课程当前无可选上课班（0 个班）`, terminal: true };
    const cand = rows
      .map((r) => ({ r, c: capacity(r) }))
      .filter((x) => !x.c.lim || x.c.left > 0)
      .sort((a, b) => b.c.left - a.c.left)[0];
    if (!cand) return { error: `全部上课班均已满（共 ${rows.length} 个）`, terminal: true };
    skb = cand.r.skbjdm;
    skbzdm = cand.r.skbzdm || '';
    outnumber = cand.r.outnumber || '0';
    zfx = cand.r.zfx_m || '0';
  }
  return { plain: buildElectPlain(f, { skbjdm: skb, skbzdm, outnumber, zfx_m: zfx }), skb };
}

async function cmdGrab(sess, arg, { conc = 1, skb = '', dry = false, retry = 1 }) {
  const { kcdm, skbjdm: fromArg } = resolveTarget(arg);
  const target = skb || fromArg;
  // Backward-compatible flags are accepted but must never cause repeated
  // submissions to an official school endpoint.
  if (conc !== 1 || retry !== 1) {
    console.log('提交保护已忽略旧并发/重试参数：同一课程最多一次物理写入');
  }

  let first = null;
  if (!dry) {
    if (process.env.KINGO_ALLOW_SINGLE_SUBMIT !== '1') {
      return { code: 'BLOCKED', msg: '真实提交默认关闭；需官方验收和明确授权' };
    }
    first = await initSession(sess);
    if (String(sess.status) !== '200' || String(first.isValidTimerange) !== '1'
        || !first.xh || first.xn == null || first.xqM == null) {
      return { code: 'BLOCKED', msg: '官方选课窗口或本人身份未通过校验' };
    }
  }

  let xf = '';
  try {
    const { rows } = await fetchCourseList(sess);
    const hit = rows.find((r) => (r.skbjdm || '').startsWith(kcdm) || (r.kc || '').includes(kcdm));
    if (hit) xf = hit.xf || '';
  } catch { /* Only the offline display may proceed without this evidence. */ }
  console.log(`目标 kcdm=${kcdm}  skbjdm=${target || '(自动选择)'}${dry ? '  DRY' : ''}`);
  const prep = await prepareElect(sess, kcdm, target, xf);
  if (prep.error) {
    console.log('终止：' + prep.error);
    return { code: prep.terminal ? 'NOCLASS' : 'ERR', msg: prep.error };
  }
  console.log(`命中上课班: ${prep.skb}`);
  if (dry) {
    console.log('[DRY] POST /jw/common/saveElectiveCourse.action');
    console.log('[DRY] 明文参数:\n' + prep.plain.split('&').map((s) => '  ' + s).join('\n'));
    return { code: 'DRY' };
  }

  // Before reserving the shared course-level intent, refresh the official
  // response. Reject account/semester changes during preparation.
  const fresh = await initSession(sess);
  if (String(first.xh) !== String(fresh.xh)
      || String(first.xn) !== String(fresh.xn)
      || String(first.xqM) !== String(fresh.xqM)) {
    return { code: 'BLOCKED', msg: '准备阶段账号或学期已变化' };
  }
  const scope = nodeScopeFromWindow({
    school: BASE, response: fresh, status: sess.status,
    expectedAccount: String(first.xh),
    canonicalTerm: process.env.KINGO_CANONICAL_TERM,
    observedTerm: process.env.KINGO_SOURCE_TERM,
    courseId: kcdm, classId: prep.skb,
  });
  const result = await submitElect(sess, prep.plain, scope);
  console.log(`提交状态 ${result.code}：${result.msg}`);
  return result;
}

// ============ 命令: watch ============
async function cmdWatch(sess, arg, interval = 3) {
  const { kcdm } = resolveTarget(arg);
  console.log(`轮询 ${kcdm}，间隔 ${interval}s，出现余量立即抢。Ctrl+C 停止。`);
  for (let i = 1; ; i++) {
    try {
      const { rows } = await fetchCourseList(sess);
      const hit = rows.filter((r) => (r.skbjdm || '').startsWith(kcdm) || (r.kc || '').includes(kcdm));
      const avail = hit.map((r) => ({ r, c: capacity(r) })).filter((x) => x.c.lim && x.c.left > 0);
      const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
      if (!hit.length) console.log(`[${t}] #${i} 列表里没有该课程`);
      else if (!avail.length) {
        console.log(`[${t}] #${i} 无余量（${hit.map((r) => `${r.skbjdm}:${capacity(r).picked}/${capacity(r).lim}`).join(' ')}）`);
      } else {
        console.log(`[${t}] #${i} ★ 出现余量 ${avail.map((x) => `${x.r.skbjdm}(余${x.c.left})`).join(' ')} → 立即提交`);
        // 命中即用该班，不再自动挑班，省一次往返
        const r = await cmdGrab(sess, kcdm, {
          conc: 1,
          skb: avail[0].r.skbjdm,
          retry: 1,
        });
        if (r && (r.code === 'PENDING' || r.code === 'BLOCKED')) return;
      }
    } catch (e) {
      // After an unknown write or a persistence/identity failure, do not
      // silently loop back into another enrollment preparation cycle.
      console.log(`  轮询终止: ${e.message}`);
      return;
    }
    await sleep(interval * 1000);
  }
}

// ============ 命令: windows（全部轮次） ============
const XKTYPE = {
  1: '预选', 2: '正选', 3: '补选', 4: '重修选',
  5: '退选', 6: '取消正选', 7: '正选实验',
};

async function cmdWindows(sess) {
  const parse = (s) => new Date(String(s).replace(/-/g, '/')).getTime();
  const c = await clockOffset(sess);
  const srvNow = now() + (c ? c.offset : 0);
  const fmt = (ms) => {
    const d = Math.floor(Math.abs(ms) / 86400000), h = Math.floor((Math.abs(ms) % 86400000) / 3600000);
    const m = Math.floor((Math.abs(ms) % 3600000) / 60000);
    return `${d}天${h}时${m}分`;
  };
  console.log('轮次        状态     开始                 结束                 说明');
  console.log('─'.repeat(88));
  for (const t of Object.keys(XKTYPE)) {
    const r = await post(`/jw/common/getWsxkTimeRange.action?xktype=${t}`, '', sess,
      BASE + '/student/wsxk.zx.html?menucode=' + MENUCODE);
    const j = parseJSON(r.text) || {};
    let res = j.result;
    if (typeof res === 'string') { try { res = JSON.parse(res); } catch { res = null; } }
    const open = String(j.status) === '200';
    let st = open ? '★开放中' : '关闭';
    if (!res?.qssj) st = '未定义';
    let note = j.message || '';
    if (open && res?.jssj) note = '剩余 ' + fmt(parse(res.jssj) - srvNow);
    else if (res?.qssj && srvNow < parse(res.qssj)) note = '未开始，还有 ' + fmt(parse(res.qssj) - srvNow);
    console.log(
      `xktype=${t} ${XKTYPE[t]}`.padEnd(14) + st.padEnd(8) +
      String(res?.qssj || '-').padEnd(20) + String(res?.jssj || '-').padEnd(20) + note
    );
    await sleep(250);
  }
  console.log(`\n服务器时间 ${new Date(srvNow).toLocaleString('zh-CN', { hour12: false })}   时钟偏差 ${c ? c.offset : 0} ms`);
}

// ============ 命令: my / drop ============
async function cmdMy(sess) {
  // tableId 每次渲染都会变，必须从退选页现取
  const page = await get('/student/wsxk.tx.nopre.html?menucode=' + MENUCODE, sess);
  const tid = (page.text.match(/DataTable\.jsp\?tableId=(\d+)/) || [, ''])[1] || '6093';
  const plain = [
    `xn=${sess.xn}`, `xq=${sess.xq}`, `xh=${sess.xh}`, 'xktype=5', `lcid=${sess.lcid}`, 'items=',
  ].join('&');
  const body = await seal(plain, sess);
  const r = await get(`/taglib/DataTable.jsp?tableId=${tid}&${body}`, sess,
    BASE + '/student/wsxk.tx.nopre.html?menucode=' + MENUCODE);
  const rows = parseRows(r.text);
  const total = (r.text.match(/showTotalRecord\(\s*'?\d+'?\s*,\s*'?(\d+)'?\s*\)/) || [, '?'])[1];
  if (!rows.length) return console.log(`已选课程 0 条（tableId=${tid} total=${total}）`);
  console.log(`已选 ${total} 条：`);
  for (const x of rows) {
    console.log(`  ${x.kcdm || x.skbjdm || ''}  ${x.kcmc || ''}  ${x.xf || ''} 学分  ${(x.rkjs || '').trim()}  ${x.sksj || ''}`);
  }
}

async function cmdDrop(sess, kcdm, skbjdm, skbzdm) {
  const plain = [
    `xn=${sess.xn}`, `xq=${sess.xq}`, `xh=${sess.xh}`, `kcdm=${kcdm}`,
    `skbjdm=${skbjdm}`, `skbzdm=${skbzdm || ''}`, `lcid=${sess.lcid}`, 'xktype=5',
  ].join('&');
  const body = await seal(plain, sess);
  const r = await post('/jw/common/cancelElectiveCourse.action', body, sess,
    BASE + '/student/wsxk.tx.nopre.html?menucode=' + MENUCODE);
  console.log(verdict(r.text));
  console.log(r.text.slice(0, 300));
}

async function cmdRaw(sess, p, plain) {
  // Raw is a read-only diagnostic tool, not an alternate write path.
  const allowed = new Set([
    '/jw/common/getWsxkTimeRange.action',
    '/jw/common/getSelectLessonScoreKcsInfo.action',
    '/taglib/DataTable.jsp',
  ]);
  if (!allowed.has(String(p || '').split('?')[0])) {
    throw new Error('raw 只允许已审核只读查询；提交需经过持久化保护');
  }
  const body = plain.startsWith('params=') ? plain : await seal(plain, sess);
  const r = await post(p, body, sess, BASE + '/student/wsxk.zx.html?menucode=' + MENUCODE);
  console.log('HTTP', r.status, r.text.length + 'B');
  console.log(r.text.slice(0, 1500));
}

// ============ 命令: probe ============
async function cmdProbe(sess) {
  const eps = [
    ['GET', '/frame/homepage?method=getTempDeskey'],
    ['GET', '/frame/homepage?method=getTempNowtime'],
    ['POST', '/jw/common/getWsxkTimeRange.action?xktype=2'],
    ['POST', '/jw/common/getSelectLessonScoreKcsInfo.action'],
    ['GET', '/student/wsxk.zx.html?menucode=' + MENUCODE],
  ];
  for (const [m, p] of eps) {
    try {
      const r = m === 'GET' ? await get(p, sess) : await post(p, '', sess, BASE + '/student/wsxk.zx.html');
      const tag = r.text.includes('凭证已失效') ? 'DENY(拦截)' : r.status === 200 ? 'OPEN' : 'HTTP' + r.status;
      console.log(`  ${tag.padEnd(12)} ${m} ${p}  ${r.text.length}B`);
    } catch (e) {
      console.log(`  ERR          ${m} ${p}  ${e.message}`);
    }
  }
}

// ============ 入口 ============
const [cmd, ...args] = process.argv.slice(2);
const opt = (k, d) => {
  const i = args.findIndex((a) => a === '--' + k || a.startsWith('--' + k + '='));
  if (i < 0) return d;
  const a = args[i];
  return a.includes('=') ? a.split('=').slice(1).join('=') : d;
};
const has = (k) => args.some((a) => a === '--' + k || a.startsWith('--' + k + '='));

(async () => {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

  if (!cmd || cmd === '-h' || cmd === '--help') {
    console.log(fs.readFileSync(fileURLToPath(import.meta.url), 'utf8').split('*/')[0].replace(/^#!.*\n\/\*\*/, ''));
    return;
  }
  if (cmd === 'import') return cmdImport(args[0]);

  const sess = loadSess();
  switch (cmd) {
    case 'probe': return cmdProbe(sess);
    case 'init': return cmdInit(sess);
    case 'calib': return cmdCalib(sess);
    case 'time': return cmdTime(sess);
    case 'windows': return cmdWindows(sess);
    case 'list': return cmdList(sess, args[0] || '');
    case 'skb': return cmdSkb(sess, args[0], opt('xf', ''));
    case 'grab':
      return cmdGrab(sess, args[0], {
        conc: Number(opt('conc', 1)) || 1,
        skb: opt('skb', ''),
        dry: has('dry'),
        retry: Number(opt('retry', 1)) || 1,
      });
    case 'watch': return cmdWatch(sess, args[0], Number(args[1]) || 3);
    case 'my': return cmdMy(sess);
    case 'drop': return cmdDrop(sess, args[0], args[1], args[2]);
    case 'raw': return cmdRaw(sess, args[0], args.slice(1).join(' '));
    default:
      console.error('未知命令: ' + cmd + '（跑 node paoke.mjs 看帮助）');
      process.exit(1);
  }
})().catch((e) => {
  console.error('失败:', e.message);
  process.exit(1);
});

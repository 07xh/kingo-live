#!/usr/bin/env node
/**
 * har-dump.mjs — 从浏览器 HAR 里剥离目标站响应体，落盘成可直接读的 html/json。
 *
 * 用途：把一次真实会话的页面（DataTable.jsp / *.action / *.jsp）原样取出，
 *      用于离线比对 tableId、ActionForm 字段、菜单树，不必反复登录。
 *
 * 用法:
 *   node tools/har-dump.mjs <xxx.har> [outDir] [--host jwmis] [--headers]
 *
 *   默认 outDir = ./dump，默认 --host 匹配 HAR 内每个域名的关键字
 *   --headers 额外打印首个命中请求的请求头（Cookie/Token 自动打码）
 *
 * 安全：只读取、只写本地文件；不替换 Cookie、不重放请求。落盘内容可能含
 *      会话标识，dump 目录已在 .gitignore 中排除，不要提交。
 */
import fs from 'node:fs';
import path from 'node:path';

const [, , harPath, ...rest] = process.argv;
if (!harPath) {
  console.error('usage: node tools/har-dump.mjs <xxx.har> [outDir] [--host kw] [--headers]');
  process.exit(1);
}

let outDir = null;
let host = 'jwmis';
let showHeaders = false;
for (let i = 0; i < rest.length; i++) {
  const a = rest[i];
  if (a === '--headers') showHeaders = true;
  else if (a === '--host') host = rest[++i] ?? host;
  else if (a.startsWith('--host=')) host = a.slice(7);
  else if (a.startsWith('--')) { console.error(`unknown flag ${a}`); process.exit(1); }
  else if (!outDir) outDir = a;
}
outDir = outDir || './dump';

const SECRET = /cookie|token|authorization|set-cookie|ticket|password/i;
const mask = (v) => {
  const s = String(v);
  if (s.length <= 12) return '<redacted>';
  return s.slice(0, 6) + '...' + s.slice(-4) + ` (len=${s.length})`;
};

const har = JSON.parse(fs.readFileSync(harPath, 'utf8'));
const entries = (har.log?.entries ?? []).filter((e) => e.request?.url?.includes(host));
if (!entries.length) {
  console.error(`no entry matching host keyword "${host}"`);
  process.exit(2);
}

fs.mkdirSync(outDir, { recursive: true });
let n = 0;
for (const e of entries) {
  const c = e.response?.content;
  if (!c || (!c.text && !c.encoding)) continue;
  n++;
  const u = new URL(e.request.url);
  const ext = /json/.test(c.mimeType || '') ? '.json' : '.html';
  const name = String(n).padStart(3, '0') + '_' + u.pathname.replace(/\//g, '_').slice(0, 70) + ext;
  const buf = c.encoding === 'base64' ? Buffer.from(c.text, 'base64') : Buffer.from(c.text || '', 'utf8');
  fs.writeFileSync(path.join(outDir, name), buf);
}

console.log(`host="${host}" 命中 ${entries.length} 条，落盘 ${n} 个文件 -> ${outDir}/`);
if (showHeaders) {
  console.log('=== 首个命中请求 ===');
  const u = new URL(entries[0].request.url);
  console.log(entries[0].request.method, u.origin + u.pathname);
  for (const h of entries[0].request.headers) {
    console.log('  ', h.name + ':', SECRET.test(h.name) ? mask(h.value) : String(h.value).slice(0, 150));
  }
}

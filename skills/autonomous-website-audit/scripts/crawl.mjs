#!/usr/bin/env node
// Website-audit crawler. Loads one or more pages of the app under test across a
// viewport x theme matrix, records machine-checkable defects as JSON, and
// writes full-page screenshots. Headless only: it never touches the user's screen.
//
//   node crawl.mjs capture --base http://localhost:3000 --path /dashboard \
//        [--storage .website-audit/auth/coach.json] [--label coach] \
//        [--viewports phone,tablet,desktop] [--themes light,dark] \
//        [--theme-storage-key theme] [--locale fr] [--out .website-audit/shots] \
//        [--no-screenshot] [--no-scroll] [--settle 800] [--timeout 30000] \
//        [--wait-for "main"] [--strict]
//   node crawl.mjs login --base http://localhost:3000 --login-path /login \
//        --email coach@example.test --password-env WEBSITE_AUDIT_PASSWORD \
//        --out .website-audit/auth/coach.json
//   node crawl.mjs selftest
//
// Viewports: phone=390x844, tablet=768x1024, desktop=1440x900, or WIDTHxHEIGHT.

import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

const VIEWPORTS = {
  phone: { width: 390, height: 844, isMobile: true, hasTouch: true },
  tablet: { width: 768, height: 1024, isMobile: false, hasTouch: true },
  desktop: { width: 1440, height: 900, isMobile: false, hasTouch: false },
};

// ---------------------------------------------------------------- arguments

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) { args._.push(a); continue; }
    const key = a.slice(2);
    if (key.startsWith('no-')) { args[key.slice(3)] = false; continue; }
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) { args[key] = true; continue; }
    i++;
    if (key === 'path') (args.path ||= []).push(next);
    else args[key] = next;
  }
  return args;
}

function die(msg, code = 2) { console.error(`crawl.mjs: ${msg}`); process.exit(code); }

function loadPlaywright() {
  // Prefer the target project's own Playwright, then the copy installed next to this script.
  for (const base of [process.cwd(), HERE]) {
    for (const mod of ['playwright', '@playwright/test']) {
      try { return createRequire(path.join(base, 'noop.js'))(mod); } catch { /* try next */ }
    }
  }
  die(`Playwright not found. Run: bash ${path.join(HERE, 'setup.sh')}`);
}

function resolveViewport(name) {
  if (VIEWPORTS[name]) return { name, ...VIEWPORTS[name] };
  const m = /^(\d+)x(\d+)$/.exec(name);
  if (!m) die(`unknown viewport "${name}" (use phone, tablet, desktop or WIDTHxHEIGHT)`);
  const width = Number(m[1]);
  return { name, width, height: Number(m[2]), isMobile: width < 600, hasTouch: width < 1024 };
}

const slug = (p) => (p.replace(/^\/+|\/+$/g, '').replace(/[^a-zA-Z0-9._-]+/g, '_') || 'root').slice(0, 120);

// ------------------------------------------------------- network noise rules

const HMR_RE = /webpack-hmr|hot-update|__vite_ping|@vite\/client|sockjs-node|__nextjs_original-stack-frame|_next\/webpack|turbopack|livereload/;
const TELEMETRY_RE = /google-analytics|googletagmanager|doubleclick|sentry\.io|hotjar|segment\.(io|com)|mixpanel|posthog|clarity\.ms|facebook\.net|plausible/;
const EXTERNAL_NET = /ERR_CONNECTION_RESET|ERR_CONNECTION_CLOSED|ERR_TIMED_OUT|ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|ERR_CONNECTION_REFUSED/;

// Returns a reason string when a failure is very likely noise, else null.
// Noise is still reported; it is only labelled so it is not mistaken for a bug.
function classifyFailure({ url, resourceType, errorText = '', status }, baseOrigin) {
  let origin = '';
  try { origin = new URL(url).origin; } catch { /* data: etc. */ }
  if (HMR_RE.test(url)) return 'dev-server-hmr (re-run once; real only if it persists)';
  if (TELEMETRY_RE.test(url)) return 'third-party-telemetry';
  if (/ERR_ABORTED/.test(errorText) && resourceType === 'media') return 'aborted-media-preload';
  if (/ERR_ABORTED/.test(errorText) && /[?&]_rsc=/.test(url)) return 'aborted-prefetch';
  if (/ERR_ABORTED/.test(errorText) && resourceType === 'fetch' && /prefetch/i.test(url)) return 'aborted-prefetch';
  if (origin && origin !== baseOrigin && EXTERNAL_NET.test(errorText)) return 'external-cdn-or-network';
  if (status === 404 && /\.map(\?|$)/.test(url)) return 'missing-sourcemap';
  if (status === 404 && /favicon|apple-touch-icon/.test(url)) return 'missing-icon (minor)';
  return null;
}

// Strip query values so tokens in query strings never land in reports.
function redactUrl(u) {
  try {
    const url = new URL(u);
    for (const k of [...url.searchParams.keys()]) url.searchParams.set(k, '…');
    return url.toString();
  } catch { return String(u).slice(0, 300); }
}

// ------------------------------------------------------ in-page DOM audit
// Runs inside the browser. Must be self-contained.

function domAudit({ maxFindings, checkContrast }) {
  const out = { overflow: {}, badText: [], rawKeys: [], brokenImages: [], lowContrast: [], storage: {}, links: [] };
  const vw = document.documentElement.clientWidth;

  const sel = (el) => {
    const parts = [];
    for (let n = el, i = 0; n && n.nodeType === 1 && i < 4; n = n.parentElement, i++) {
      let s = n.tagName.toLowerCase();
      if (n.id) { s += `#${n.id}`; parts.unshift(s); break; }
      const cls = typeof n.className === 'string' ? n.className.trim().split(/\s+/).filter(Boolean).slice(0, 2) : [];
      if (cls.length) s += '.' + cls.join('.');
      parts.unshift(s);
    }
    return parts.join(' > ');
  };
  const visible = (el) => {
    if (!el || !el.getClientRects().length) return false;
    if (el.checkVisibility) return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const push = (arr, item) => { if (arr.length < maxFindings) arr.push(item); };

  // Horizontal overflow: does the page scroll sideways, and which outermost elements stick out?
  const se = document.scrollingElement || document.documentElement;
  out.overflow.pageScrollsHorizontally = se.scrollWidth > se.clientWidth + 1;
  out.overflow.scrollWidth = se.scrollWidth;
  out.overflow.clientWidth = se.clientWidth;
  const offenders = new Set();
  const clipsX = (el) => {
    for (let p = el.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
      if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) return true;
    }
    return false;
  };
  const all = document.body ? document.body.querySelectorAll('*') : [];
  for (let i = 0; i < all.length && i < 8000; i++) {
    const el = all[i];
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right > vw + 1 || r.left < -1) {
      if (getComputedStyle(el).position === 'fixed') continue;
      if (clipsX(el) || !visible(el)) continue;
      if (el.parentElement && offenders.has(el.parentElement)) { offenders.add(el); continue; }
      offenders.add(el);
      if (!out.overflow.offenders) out.overflow.offenders = [];
      push(out.overflow.offenders, { selector: sel(el), left: Math.round(r.left), right: Math.round(r.right), viewport: vw });
    }
  }

  // Visible text: NaN/undefined/null leaks and raw i18n keys or unfilled placeholders.
  const BAD = /(^|[^\w])(NaN|undefined|null|Infinity|\[object Object\]|Invalid Date)(?=[^\w]|$)/;
  const NOT_KEY_TAIL = /^(com|org|net|io|dev|app|ai|co|fr|de|uk|us|tn|js|mjs|ts|tsx|jsx|json|png|jpe?g|svg|webp|gif|pdf|csv|html?|css|md|txt|zip|mp4|webm|py|rb|php|sh|yml|yaml|lock)$/i;
  const looksLikeKey = (t) => {
    if (t.length > 80 || /[\s@/:]/.test(t)) return false;
    if (!/^[A-Za-z][\w-]*(\.[\w-]+)+$/.test(t)) return false;
    const segs = t.split('.');
    if (NOT_KEY_TAIL.test(segs[segs.length - 1])) return false;
    return segs.length >= 3 || /[a-z][A-Z]|_/.test(t) || segs.every((s) => /^[a-z]/.test(s));
  };
  const PLACEHOLDER = /\{\{\s*[\w.]+\s*\}\}|(^|[^$\w])\{[a-zA-Z_][\w]*(,\s*(plural|select|number)[^}]*)?\}|MISSING_MESSAGE|translation missing|\[missing ".*?" translation\]/;
  const checkText = (t, el, where) => {
    const m = BAD.exec(t);
    if (m) push(out.badText, { match: m[2], text: t.slice(0, 140), selector: sel(el), where });
    if (looksLikeKey(t)) push(out.rawKeys, { text: t, selector: sel(el), where, kind: 'raw-key' });
    else if (PLACEHOLDER.test(t)) push(out.rawKeys, { text: t.slice(0, 140), selector: sel(el), where, kind: 'unfilled-placeholder' });
  };
  if (document.body) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => {
        const p = n.parentElement;
        if (!p || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|CODE|PRE)$/.test(p.tagName)) return NodeFilter.FILTER_REJECT;
        return n.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (visible(n.parentElement)) checkText(n.textContent.trim(), n.parentElement, 'text');
    }
    for (const el of document.querySelectorAll('[placeholder],[aria-label],img[alt],[title]')) {
      for (const attr of ['placeholder', 'aria-label', 'alt', 'title']) {
        const v = el.getAttribute(attr);
        if (v && v.trim()) checkText(v.trim(), el, `@${attr}`);
      }
    }
    out.title = document.title;
    if (document.title) checkText(document.title, document.documentElement, 'document.title');
  }

  for (const img of document.images) {
    if (img.complete && img.naturalWidth === 0 && img.getAttribute('src') && visible(img)) {
      push(out.brokenImages, { src: img.currentSrc || img.src, selector: sel(img) });
    }
  }

  // Contrast of visible text against its effective solid background (WCAG AA).
  if (checkContrast && document.body) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const cache = new Map();
    const rgba = (css) => {
      if (cache.has(css)) return cache.get(css);
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = '#000';
      ctx.fillStyle = css; // canvas converts oklch/lab/color() to sRGB for us
      ctx.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
      const v = { r, g, b, a: a / 255 };
      cache.set(css, v);
      return v;
    };
    const over = (top, bottom) => ({
      r: top.r * top.a + bottom.r * (1 - top.a),
      g: top.g * top.a + bottom.g * (1 - top.a),
      b: top.b * top.a + bottom.b * (1 - top.a),
      a: 1,
    });
    const background = (el) => {
      const layers = [];
      for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
        const cs = getComputedStyle(n);
        if (cs.backgroundImage && cs.backgroundImage !== 'none') return null; // gradient/image: unknown
        const c = rgba(cs.backgroundColor);
        if (c.a > 0) { layers.push(c); if (c.a >= 1) break; }
      }
      let acc = { r: 255, g: 255, b: 255, a: 1 };
      for (let i = layers.length - 1; i >= 0; i--) acc = over(layers[i], acc);
      return acc;
    };
    const lum = ({ r, g, b }) => {
      const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const seen = new Set();
    for (const el of document.body.querySelectorAll('*')) {
      if (out.lowContrast.length >= maxFindings) break;
      const own = [...el.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim());
      if (!own || !visible(el)) continue;
      const cs = getComputedStyle(el);
      const bg = background(el);
      if (!bg) continue;
      const fg = over(rgba(cs.color), bg);
      const L1 = lum(fg), L2 = lum(bg);
      const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
      const size = parseFloat(cs.fontSize);
      const large = size >= 24 || (size >= 18.66 && Number(cs.fontWeight) >= 700);
      const need = large ? 3 : 4.5;
      if (ratio < need) {
        const key = `${cs.color}|${sel(el)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.lowContrast.push({
          ratio: Math.round(ratio * 100) / 100, need, color: cs.color, selector: sel(el),
          text: el.textContent.trim().slice(0, 60), disabled: el.closest(':disabled,[aria-disabled="true"]') !== null,
        });
      }
    }
  }

  // Storage: key names and flags only; values never leave the page.
  const SENSITIVE = /token|jwt|auth|session|access|refresh|secret|password|bearer|credential/i;
  const JWT = /eyJ[\w-]+\.[\w-]+\.[\w-]*/;
  for (const [name, store] of [['localStorage', window.localStorage], ['sessionStorage', window.sessionStorage]]) {
    const items = [];
    try {
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i);
        const v = store.getItem(k) || '';
        const flags = [];
        if (SENSITIVE.test(k)) flags.push('sensitive-name');
        if (JWT.test(v)) flags.push('jwt-value');
        items.push({ key: k, length: v.length, flags });
      }
    } catch { /* storage blocked */ }
    out.storage[name] = items;
  }

  const links = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    try {
      const u = new URL(a.getAttribute('href'), location.href);
      if (u.origin === location.origin) links.add(u.pathname + u.search);
    } catch { /* bad href */ }
  }
  out.links = [...links].sort().slice(0, 400);
  out.lang = document.documentElement.lang || null;
  out.dir = document.documentElement.dir || getComputedStyle(document.documentElement).direction;
  return out;
}

// --------------------------------------------------------------- capture

async function autoScroll(page) {
  await page.evaluate(async () => {
    const step = Math.max(200, Math.floor(window.innerHeight * 0.8));
    for (let i = 0; i < 40; i++) {
      const before = window.scrollY;
      window.scrollBy(0, step);
      await new Promise((r) => setTimeout(r, 120));
      if (window.scrollY === before) break;
    }
    window.scrollTo(0, 0);
  });
}

async function vitals(page) {
  return page.evaluate(() => new Promise((resolve) => {
    const nav = performance.getEntriesByType('navigation')[0];
    const result = nav ? {
      ttfbMs: Math.round(nav.responseStart),
      domContentLoadedMs: Math.round(nav.domContentLoadedEventEnd),
      loadMs: Math.round(nav.loadEventEnd),
      transferKB: Math.round((nav.transferSize || 0) / 1024),
    } : {};
    let lcp = 0;
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) lcp = Math.max(lcp, e.startTime);
      }).observe({ type: 'largest-contentful-paint', buffered: true });
    } catch { /* unsupported */ }
    setTimeout(() => resolve({ ...result, lcpMs: Math.round(lcp) || null }), 150);
  }));
}

async function captureOne(browser, opts, pagePath, vp, theme) {
  const baseOrigin = new URL(opts.base).origin;
  const url = new URL(pagePath, opts.base).toString();
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: 1,
    isMobile: vp.isMobile,
    hasTouch: vp.hasTouch,
    colorScheme: theme,
    locale: opts.locale || undefined,
    extraHTTPHeaders: opts.locale ? { 'Accept-Language': opts.locale } : undefined,
    storageState: opts.storage || undefined,
  });
  if (opts.themeStorageKey) {
    await context.addInitScript(([k, v]) => { try { localStorage.setItem(k, v); } catch { /* blocked */ } }, [opts.themeStorageKey, theme]);
  }
  const page = await context.newPage();
  const r = {
    url, path: pagePath, label: opts.label, viewport: vp.name, size: `${vp.width}x${vp.height}`, theme,
    locale: opts.locale || null, at: new Date().toISOString(),
    pageErrors: [], consoleErrors: [], failedRequests: [], httpErrors: [], api: { calls: [], origins: [] },
  };
  page.on('pageerror', (e) => r.pageErrors.push({ message: String(e.message).slice(0, 500), stack: String(e.stack || '').split('\n').slice(0, 4).join('\n') }));
  page.on('console', (m) => {
    if (m.type() === 'error') r.consoleErrors.push({ text: m.text().slice(0, 500), source: m.location()?.url ? redactUrl(m.location().url) : null });
  });
  page.on('requestfailed', (req) => {
    const item = { url: redactUrl(req.url()), method: req.method(), resourceType: req.resourceType(), error: req.failure()?.errorText || '' };
    item.noise = classifyFailure({ ...item, url: req.url(), errorText: item.error }, baseOrigin);
    r.failedRequests.push(item);
  });
  page.on('response', (res) => {
    const status = res.status();
    if (status < 400) return;
    const req = res.request();
    const item = { url: redactUrl(res.url()), status, method: req.method(), resourceType: req.resourceType() };
    item.noise = classifyFailure({ url: res.url(), resourceType: item.resourceType, status }, baseOrigin);
    if (status === 401 || status === 403) item.note = 'verify-gating: check the backend rule before fixing; intended refusals must be explained in the UI';
    r.httpErrors.push(item);
  });
  page.on('requestfinished', async (req) => {
    const type = req.resourceType();
    if (type !== 'fetch' && type !== 'xhr') return;
    const t = req.timing();
    let status = null;
    try { status = (await req.response())?.status() ?? null; } catch { /* gone */ }
    r.api.calls.push({ method: req.method(), url: redactUrl(req.url()), status, ms: t.responseEnd >= 0 ? Math.round(t.responseEnd) : null });
  });

  try {
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: opts.timeout });
    r.status = resp ? resp.status() : null;
    try { await page.waitForLoadState('networkidle', { timeout: Math.min(opts.timeout, 15000) }); r.networkIdle = true; } catch { r.networkIdle = false; }
    if (opts.waitFor) {
      try { await page.waitForSelector(opts.waitFor, { timeout: opts.timeout }); } catch { r.waitForMissing = opts.waitFor; }
    }
    if (opts.scroll) await autoScroll(page);
    await page.waitForTimeout(opts.settle);
    r.finalUrl = page.url();
    const reqPath = new URL(url).pathname;
    const finalPath = new URL(r.finalUrl).pathname;
    r.redirected = finalPath !== reqPath;
    r.redirectedToLogin = r.redirected && /log-?in|sign-?in|auth/i.test(finalPath) && !/log-?in|sign-?in|auth/i.test(reqPath);
    r.timing = await vitals(page);
    Object.assign(r, await page.evaluate(domAudit, { maxFindings: opts.maxFindings, checkContrast: opts.contrast }));
    r.cookies = (await context.cookies(opts.base)).map((c) => ({
      name: c.name, httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite,
      flags: /token|jwt|auth|session|sid|access|refresh/i.test(c.name) && !c.httpOnly ? ['auth-cookie-readable-by-js'] : [],
    }));
    if (opts.screenshot) {
      const dir = path.join(opts.out, slug(pagePath));
      fs.mkdirSync(dir, { recursive: true });
      const file = path.join(dir, `${opts.label}__${vp.name}__${theme}${opts.locale ? `__${opts.locale}` : ''}.png`);
      try {
        await page.screenshot({ path: file, fullPage: true, animations: 'disabled' });
        r.screenshot = file;
      } catch (e) {
        await page.screenshot({ path: file, animations: 'disabled' });
        r.screenshot = file;
        r.screenshotNote = `full-page capture failed (${String(e.message).slice(0, 120)}); viewport-only capture saved`;
      }
    }
  } catch (e) {
    r.navigationError = String(e.message).slice(0, 500);
  } finally {
    await context.close();
  }

  // Summaries the agent reads first.
  const calls = r.api.calls;
  r.api.origins = [...new Set(calls.map((c) => { try { return new URL(c.url).origin; } catch { return null; } }).filter(Boolean))];
  r.api.crossOrigin = r.api.origins.filter((o) => o !== baseOrigin);
  r.api.slowest = [...calls].filter((c) => c.ms != null).sort((a, b) => b.ms - a.ms).slice(0, 10);
  const real = (arr) => arr.filter((x) => !x.noise);
  r.summary = {
    status: r.status ?? null,
    pageErrors: r.pageErrors.length,
    consoleErrors: r.consoleErrors.length,
    failedRequests: real(r.failedRequests).length,
    failedNoise: r.failedRequests.length - real(r.failedRequests).length,
    httpErrors: real(r.httpErrors).length,
    rawKeys: r.rawKeys?.length ?? 0,
    badText: r.badText?.length ?? 0,
    overflow: Boolean(r.overflow?.pageScrollsHorizontally || r.overflow?.offenders?.length),
    lowContrast: r.lowContrast?.filter((x) => !x.disabled).length ?? 0,
    brokenImages: r.brokenImages?.length ?? 0,
    tokensInStorage: [...(r.storage?.localStorage || []), ...(r.storage?.sessionStorage || [])].filter((x) => x.flags.length).length,
    apiCalls: calls.length,
    slowestApiMs: r.api.slowest[0]?.ms ?? null,
    redirectedToLogin: r.redirectedToLogin || false,
    navigationError: Boolean(r.navigationError),
  };
  return r;
}

async function capture(args) {
  const base = args.base || process.env.WEBSITE_AUDIT_BASE;
  if (!base) die('--base is required (or set WEBSITE_AUDIT_BASE)');
  if (!args.path?.length) die('--path is required (repeat it for several pages)');
  if (args.storage && !fs.existsSync(args.storage)) die(`storage state not found: ${args.storage}`);
  const opts = {
    base,
    storage: args.storage || null,
    label: args.label || (args.storage ? path.basename(args.storage).replace(/\.json$/, '') : 'anon'),
    locale: args.locale || null,
    themeStorageKey: args['theme-storage-key'] || null,
    out: args.out || '.website-audit/shots',
    screenshot: args.screenshot !== false,
    scroll: args.scroll !== false,
    contrast: args.contrast !== false,
    settle: Number(args.settle ?? 800),
    timeout: Number(args.timeout ?? 30000),
    waitFor: typeof args['wait-for'] === 'string' ? args['wait-for'] : null,
    maxFindings: Number(args['max-findings'] ?? 25),
  };
  const viewports = String(args.viewports || 'phone,tablet,desktop').split(',').map((s) => resolveViewport(s.trim()));
  const themes = String(args.themes || 'light').split(',').map((s) => s.trim());
  for (const t of themes) if (!['light', 'dark'].includes(t)) die(`theme must be light or dark, got "${t}"`);

  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ headless: true });
  const reports = [];
  let actionable = 0;
  try {
    for (const p of args.path) {
      for (const vp of viewports) {
        for (const theme of themes) {
          const r = await captureOne(browser, opts, p, vp, theme);
          reports.push(r);
          const dir = path.join(opts.out, slug(p));
          fs.mkdirSync(dir, { recursive: true });
          const file = path.join(dir, `${opts.label}__${vp.name}__${theme}${opts.locale ? `__${opts.locale}` : ''}.json`);
          fs.writeFileSync(file, JSON.stringify(r, null, 2));
          const s = r.summary;
          const n = s.pageErrors + s.consoleErrors + s.failedRequests + s.httpErrors + s.rawKeys + s.badText + s.lowContrast + s.brokenImages + s.tokensInStorage + (s.overflow ? 1 : 0) + (s.navigationError ? 1 : 0) + (s.redirectedToLogin ? 1 : 0);
          actionable += n;
          console.log(
            `[${opts.label} ${vp.name} ${theme}${opts.locale ? ' ' + opts.locale : ''}] ${p} -> ${s.status ?? 'ERR'}` +
            `${r.redirected ? ` (redirected to ${new URL(r.finalUrl).pathname})` : ''}` +
            ` | pageErrors ${s.pageErrors} console ${s.consoleErrors} failed ${s.failedRequests}(+${s.failedNoise} noise)` +
            ` http>=400 ${s.httpErrors} rawKeys ${s.rawKeys} badText ${s.badText} overflow ${s.overflow ? 'YES' : 'no'}` +
            ` lowContrast ${s.lowContrast} brokenImg ${s.brokenImages} tokensInStorage ${s.tokensInStorage}` +
            ` api ${s.apiCalls}${s.slowestApiMs != null ? ` (slowest ${s.slowestApiMs}ms)` : ''}` +
            `${r.timing?.ttfbMs != null ? ` ttfb ${r.timing.ttfbMs}ms` : ''}` +
            `${r.navigationError ? ` NAVIGATION ERROR: ${r.navigationError.split('\n')[0]}` : ''}` +
            `\n    report: ${file}${r.screenshot ? `\n    shot:   ${r.screenshot}` : ''}`,
          );
        }
      }
    }
  } finally {
    await browser.close();
  }
  if (args.strict && actionable > 0) process.exit(1);
}

// ----------------------------------------------------------------- login

async function login(args) {
  const base = args.base || process.env.WEBSITE_AUDIT_BASE;
  if (!base) die('--base is required');
  if (!args.out) die('--out <storage-state.json> is required');
  const user = args.email || args.username;
  if (!user) die('--email or --username is required');
  const envName = args['password-env'] || 'WEBSITE_AUDIT_PASSWORD';
  const password = process.env[envName];
  if (!password) die(`password not set: export ${envName}=... (passwords are never taken on the command line)`);
  const loginPath = args['login-path'] || '/login';
  const userSel = args['user-selector'] || 'input[type=email], input[name=email], input[name=username], input[autocomplete=username], input[name=login]';
  const passSel = args['password-selector'] || 'input[type=password]';
  const submitSel = args['submit-selector'] || 'button[type=submit], input[type=submit]';
  const timeout = Number(args.timeout ?? 30000);

  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(new URL(loginPath, base).toString(), { waitUntil: 'domcontentloaded', timeout });
    await page.locator(userSel).first().fill(user, { timeout });
    const pass = page.locator(passSel).first();
    if (!(await pass.isVisible().catch(() => false))) {
      // Two-step forms: submit the identifier, then the password field appears.
      await page.locator(submitSel).first().click({ timeout });
      await pass.waitFor({ state: 'visible', timeout });
    }
    await pass.fill(password, { timeout });
    await page.locator(submitSel).first().click({ timeout });
    try {
      if (args['success-selector']) {
        await page.waitForSelector(args['success-selector'], { timeout });
      } else {
        const loginPathname = new URL(loginPath, base).pathname;
        await page.waitForURL((u) => new URL(u).pathname !== loginPathname, { timeout });
      }
    } catch {
      die(`login did not succeed within ${timeout}ms (still on ${new URL(page.url()).pathname}). ` +
        'Check the credentials, or pass --success-selector if the app does not navigate away after login.', 1);
    }
    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
    fs.mkdirSync(path.dirname(path.resolve(args.out)), { recursive: true });
    await context.storageState({ path: args.out });
    fs.chmodSync(args.out, 0o600);
    console.log(`logged in as ${user}; landed on ${new URL(page.url()).pathname}; storage state -> ${args.out} (mode 600, do not commit)`);
  } finally {
    await browser.close();
  }
}

async function selftest() {
  const pw = loadPlaywright();
  const browser = await pw.chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setContent('<p>ok</p>');
  const text = await page.textContent('p');
  console.log(`playwright ok: chromium ${browser.version()}, render check "${text}"`);
  await browser.close();
}

// ------------------------------------------------------------------ main

const args = parseArgs(process.argv.slice(2));
const cmd = args._[0] || 'capture';
const run = { capture, login, selftest }[cmd];
if (!run) die(`unknown command "${cmd}" (capture | login | selftest)`);
run(args).catch((e) => die(e.stack || String(e), 1));

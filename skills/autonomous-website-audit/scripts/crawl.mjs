#!/usr/bin/env node
// Website-audit crawler. Loads one or more pages of the app under test across a
// viewport x theme matrix, records machine-checkable defects as JSON, and
// writes full-page screenshots. Headless only: it never touches the user's screen.
//
//   node crawl.mjs capture --base http://localhost:3000 --path /dashboard \
//        [--storage .website-audit/auth/coach.json] [--label coach] \
//        [--viewports phone,tablet,desktop] [--themes light,dark] \
//        [--theme-storage-key theme] [--locale fr] [--out .website-audit/shots] \
//        [--no-screenshot] [--no-scroll] [--no-axe] [--keyboard] [--settle 800] \
//        [--timeout 30000] [--wait-for "main"] [--strict]
//   node crawl.mjs discover --base http://localhost:3000 [--path /] \
//        [--storage .website-audit/auth/coach.json] [--max-pages 150] [--deny REGEX] \
//        [--compare .website-audit/progress.json] [--out .website-audit/discover]
//   node crawl.mjs login --base http://localhost:3000 --login-path /login \
//        --email coach@example.test --password-env WEBSITE_AUDIT_PASSWORD \
//        --out .website-audit/auth/coach.json
//   node crawl.mjs selftest
//
// Viewports: phone=390x844, tablet=768x1024, desktop=1440x900, or WIDTHxHEIGHT.
// discover only follows <a href> links with GET navigations; it never clicks or submits.

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

// axe-core source, from the project or from next to this script. null when absent:
// the capture still runs, and the report says the accessibility scan was skipped.
function loadAxeSource() {
  for (const base of [process.cwd(), HERE]) {
    try {
      const file = createRequire(path.join(base, 'noop.js')).resolve('axe-core/axe.min.js');
      return fs.readFileSync(file, 'utf8');
    } catch { /* try next */ }
  }
  return null;
}

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

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

// Names the console errors that have a known cause, so they are triaged correctly.
function consoleKind(text) {
  if (/hydrat|did not match|server rendered HTML|Text content does not match/i.test(text)) return 'hydration-mismatch';
  if (/ChunkLoadError|Loading chunk \S+ failed|Failed to fetch dynamically imported module/i.test(text)) return 'chunk-load (stale deploy or dev-server restart; re-run)';
  if (/Content Security Policy|Refused to (load|execute|apply|connect)/i.test(text)) return 'csp-violation';
  if (/unique "key" prop|Each child in a list/i.test(text)) return 'react-key-warning';
  if (/CORS|Access-Control-Allow-Origin/i.test(text)) return 'cors';
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

function domAudit({ maxFindings, checkContrast, checkTouch }) {
  const out = { overflow: {}, badText: [], rawKeys: [], brokenImages: [], lowContrast: [], smallTargets: [], structure: {}, storage: {}, links: [] };
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

  // Touch targets (touch viewports only). WCAG 2.2 SC 2.5.8 sets 24x24 CSS px as the floor.
  // Inline links inside running text are exempt, as are controls wrapped by a large enough label.
  if (checkTouch && document.body) {
    const CONTROLS = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=tab],[role=checkbox],[role=radio],[role=switch],[role=menuitem]';
    for (const el of document.body.querySelectorAll(CONTROLS)) {
      if (out.smallTargets.length >= maxFindings) break;
      if (!visible(el) || el.closest('[aria-hidden="true"],[inert]')) continue;
      const r = el.getBoundingClientRect();
      if (r.width >= 24 && r.height >= 24) continue;
      const cs = getComputedStyle(el);
      if (el.tagName === 'A' && cs.display === 'inline') {
        const block = el.parentElement;
        if (block && block.textContent.trim().length > el.textContent.trim().length + 10) continue;
      }
      const label = el.closest('label');
      if (label) { const lr = label.getBoundingClientRect(); if (lr.width >= 24 && lr.height >= 24) continue; }
      push(out.smallTargets, {
        selector: sel(el), width: Math.round(r.width), height: Math.round(r.height),
        text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 40),
      });
    }
  }

  // Document structure: one h1, a title, a language, unique ids. Missing descriptions only
  // matter on public pages, so they are reported but not counted.
  const h1s = [...document.querySelectorAll('h1')].filter(visible);
  const idCounts = {};
  for (const el of document.querySelectorAll('[id]')) idCounts[el.id] = (idCounts[el.id] || 0) + 1;
  const dupIds = Object.entries(idCounts).filter(([, n]) => n > 1).map(([id, n]) => `${id} x${n}`);
  out.structure = {
    h1Count: h1s.length,
    h1: h1s.slice(0, 3).map((h) => h.textContent.trim().slice(0, 80)),
    titleMissing: !document.title.trim(),
    langMissing: !document.documentElement.getAttribute('lang'),
    duplicateIds: dupIds.slice(0, maxFindings),
    metaDescription: document.querySelector('meta[name="description"]')?.getAttribute('content') || null,
    canonical: document.querySelector('link[rel="canonical"]')?.getAttribute('href') || null,
    // A noindex left on a public page hides it from search; one missing on a private page leaks it.
    robots: document.querySelector('meta[name="robots"]')?.getAttribute('content') || null,
    ogTitle: document.querySelector('meta[property="og:title"]')?.getAttribute('content') || null,
    ogImage: document.querySelector('meta[property="og:image"]')?.getAttribute('content') || null,
  };
  // Rendered design system: the fonts, sizes and text colours actually used, so drift across
  // pages (a fourth font family, a one-off hex colour) shows up in summarize.py drift.
  const tokens = { fonts: {}, fontSizes: {}, textColors: {}, sampled: 0 };
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (tokens.sampled >= 1500) break;
    if (![...el.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim())) continue;
    if (!visible(el)) continue;
    tokens.sampled += 1;
    const cs = getComputedStyle(el);
    const fam = cs.fontFamily.split(',')[0].replace(/["']/g, '').trim();
    tokens.fonts[fam] = (tokens.fonts[fam] || 0) + 1;
    tokens.fontSizes[cs.fontSize] = (tokens.fontSizes[cs.fontSize] || 0) + 1;
    tokens.textColors[cs.color] = (tokens.textColors[cs.color] || 0) + 1;
  }
  out.designTokens = tokens;
  out.structure.issues = [
    h1s.length === 0 && 'no visible <h1>',
    h1s.length > 1 && `${h1s.length} visible <h1> elements`,
    out.structure.titleMissing && 'empty <title>',
    out.structure.langMissing && '<html> has no lang',
    dupIds.length && `${dupIds.length} duplicated id(s)`,
  ].filter(Boolean);

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
  const vpMeta = document.querySelector('meta[name="viewport"]');
  out.viewportMeta = vpMeta ? vpMeta.getAttribute('content') : null;
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
    // Page weight by type, from resource timing (transferSize is 0 for cached or cross-origin
    // resources without Timing-Allow-Origin, so this is a floor, not an exact total).
    const weight = { jsKB: 0, cssKB: 0, imgKB: 0, fontKB: 0, otherKB: 0, requests: 0 };
    for (const e of performance.getEntriesByType('resource')) {
      const kb = (e.transferSize || e.encodedBodySize || 0) / 1024;
      const t = e.initiatorType;
      const u = e.name.split('?')[0];
      const key = /\.m?js$/.test(u) || t === 'script' ? 'jsKB' : /\.css$/.test(u) || (t === 'link' && /css/.test(u)) ? 'cssKB'
        : t === 'img' || /\.(png|jpe?g|gif|webp|avif|svg)$/.test(u) ? 'imgKB' : /\.(woff2?|ttf|otf)$/.test(u) ? 'fontKB' : 'otherKB';
      weight[key] += kb;
      weight.requests += 1;
    }
    for (const k of Object.keys(weight)) if (k !== 'requests') weight[k] = Math.round(weight[k]);
    result.weight = weight;
    let lcp = 0;
    let cls = 0;
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) lcp = Math.max(lcp, e.startTime);
      }).observe({ type: 'largest-contentful-paint', buffered: true });
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) if (!e.hadRecentInput) cls += e.value;
      }).observe({ type: 'layout-shift', buffered: true });
    } catch { /* unsupported */ }
    setTimeout(() => resolve({ ...result, lcpMs: Math.round(lcp) || null, cls: Math.round(cls * 1000) / 1000 }), 150);
  }));
}

// WCAG A/AA rules only, so the findings are ones a fix can close. Evaluated through the
// DevTools protocol, so the page's CSP does not block it.
async function axeScan(page, source, maxFindings) {
  await page.evaluate(source);
  const res = await page.evaluate(async (tags) => {
    // eslint-disable-next-line no-undef
    const r = await axe.run(document, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] });
    return r.violations.map((v) => ({
      id: v.id, impact: v.impact, help: v.help, helpUrl: v.helpUrl, nodes: v.nodes.length,
      targets: v.nodes.slice(0, 5).map((n) => n.target.join(' ')),
      sample: (v.nodes[0]?.html || '').slice(0, 160),
    }));
  }, AXE_TAGS);
  const rank = { critical: 0, serious: 1, moderate: 2, minor: 3 };
  return res.sort((a, b) => (rank[a.impact] ?? 4) - (rank[b.impact] ?? 4)).slice(0, maxFindings);
}

// Tabs through the page and records what keyboard users get: whether focus is visible,
// whether it gets stuck, and which pointer-only controls it can never reach.
async function keyboardProbe(page, maxStops) {
  const FOCUSABLE = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[tabindex],[contenteditable=""],[contenteditable=true]';
  await page.evaluate((focusable) => {
    const look = (el) => { const c = getComputedStyle(el); return [c.outlineStyle, c.outlineWidth, c.outlineColor, c.boxShadow, c.borderColor, c.backgroundColor, c.color, c.textDecorationLine].join('|'); };
    window.__auditFocus = [];
    document.querySelectorAll(focusable).forEach((el, i) => { el.dataset.auditFi = String(i); window.__auditFocus[i] = look(el); });
    document.activeElement?.blur?.();
  }, FOCUSABLE);
  const stops = [];
  let trap = null;
  for (let i = 0; i < maxStops; i++) {
    await page.keyboard.press('Tab');
    const s = await page.evaluate(async () => {
      const el = document.activeElement;
      if (!el || el === document.body || el === document.documentElement) return { body: true };
      // Let a focus transition finish before reading the focused style.
      const longest = Math.max(0, ...getComputedStyle(el).transitionDuration.split(',').map((d) => parseFloat(d) * (d.includes('ms') ? 1 : 1000) || 0));
      if (longest) await new Promise((r) => setTimeout(r, Math.min(longest + 20, 400)));
      const c = getComputedStyle(el);
      const now = [c.outlineStyle, c.outlineWidth, c.outlineColor, c.boxShadow, c.borderColor, c.backgroundColor, c.color, c.textDecorationLine].join('|');
      const before = el.dataset.auditFi !== undefined ? window.__auditFocus[Number(el.dataset.auditFi)] : null;
      const outlined = c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0;
      const r = el.getBoundingClientRect();
      const p = [];
      for (let n = el, k = 0; n && n.nodeType === 1 && k < 3; n = n.parentElement, k++) {
        p.unshift(n.tagName.toLowerCase() + (n.id ? `#${n.id}` : ''));
        if (n.id) break;
      }
      return {
        key: el.dataset.auditFi ?? p.join('>'), selector: p.join(' > '),
        text: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim().slice(0, 40),
        indicator: outlined || (before !== null && before !== now),
        offscreen: r.width === 0 || r.height === 0 || r.bottom + window.scrollY < 0 || r.right < 0,
      };
    });
    if (s.body) { if (stops.length) break; continue; }
    if (stops.length && s.key === stops[0].key) break; // wrapped around: full cycle
    stops.push(s);
    if (stops.filter((x) => x.key === s.key).length > 2) { trap = s.selector; break; }
  }
  const pointerOnly = await page.evaluate((focusable) => {
    const out = [];
    for (const el of document.body.querySelectorAll('*')) {
      if (out.length >= 15) break;
      if (getComputedStyle(el).cursor !== 'pointer') continue;
      if (el.closest(focusable) || el.closest('label')) continue;
      if (el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer') continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      out.push({ tag: el.tagName.toLowerCase(), text: (el.innerText || '').trim().slice(0, 40), cls: String(el.className).slice(0, 60) });
    }
    return out;
  }, FOCUSABLE);
  return {
    stops: stops.length,
    trap,
    firstStop: stops[0] ? `${stops[0].selector} "${stops[0].text}"` : null,
    skipLink: Boolean(stops[0] && /skip/i.test(stops[0].text)),
    noIndicator: stops.filter((s) => !s.indicator).slice(0, 20).map((s) => `${s.selector} "${s.text}"`),
    offscreenFocus: stops.filter((s) => s.offscreen).slice(0, 10).map((s) => s.selector),
    pointerOnly,
  };
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
    reducedMotion: 'reduce', // steadier captures; animations are reviewed by hand, not here
    locale: opts.locale || undefined,
    extraHTTPHeaders: opts.locale ? { 'Accept-Language': opts.locale } : undefined,
    storageState: opts.storage || undefined,
  });
  if (opts.themeStorageKey) {
    await context.addInitScript(([k, v]) => { try { localStorage.setItem(k, v); } catch { /* blocked */ } }, [opts.themeStorageKey, theme]);
  }
  const page = await context.newPage();
  const r = {
    url, path: pagePath, label: opts.label, signedIn: Boolean(opts.storage), viewport: vp.name, size: `${vp.width}x${vp.height}`, theme,
    locale: opts.locale || null, at: new Date().toISOString(),
    pageErrors: [], consoleErrors: [], failedRequests: [], httpErrors: [], api: { calls: [], origins: [] },
  };
  page.on('pageerror', (e) => r.pageErrors.push({ message: String(e.message).slice(0, 500), stack: String(e.stack || '').split('\n').slice(0, 4).join('\n') }));
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const text = m.text();
    r.consoleErrors.push({ text: text.slice(0, 500), kind: consoleKind(text), source: m.location()?.url ? redactUrl(m.location().url) : null });
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
    // start is epoch ms; bench.py e2e uses it to find request waterfalls (a call that starts after another ends).
    r.api.calls.push({ method: req.method(), url: redactUrl(req.url()), status, ms: t.responseEnd >= 0 ? Math.round(t.responseEnd) : null, start: t.startTime > 0 ? Math.round(t.startTime) : null });
  });

  try {
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: opts.timeout });
    r.status = resp ? resp.status() : null;
    // Apps with websockets or polling never go idle; wait briefly, then rely on --wait-for.
    try { await page.waitForLoadState('networkidle', { timeout: opts.idleTimeout }); r.networkIdle = true; } catch { r.networkIdle = false; }
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
    Object.assign(r, await page.evaluate(domAudit, { maxFindings: opts.maxFindings, checkContrast: opts.contrast, checkTouch: vp.hasTouch }));
    if (opts.axeSource) {
      try { r.a11y = await axeScan(page, opts.axeSource, opts.maxFindings); } catch (e) { r.a11yError = String(e.message).slice(0, 200); }
    } else if (opts.axe) {
      r.a11yError = 'axe-core not installed; run setup.sh (the accessibility scan was skipped)';
    }
    r.cookies = (await context.cookies(opts.base)).map((c) => ({
      name: c.name, httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite,
      flags: /token|jwt|auth|session|sid|access|refresh/i.test(c.name) && !c.httpOnly ? ['auth-cookie-readable-by-js'] : [],
    }));
    r.scrollContainers = await page.evaluate(() => {
      const out = [];
      for (const el of document.querySelectorAll('body *')) {
        if (out.length >= 10) break;
        const cs = getComputedStyle(el);
        if (!/(auto|scroll)/.test(cs.overflowY) || el.scrollHeight <= el.clientHeight + 40 || el.clientHeight < 150) continue;
        if (el === document.scrollingElement || el === document.body) continue;
        const id = el.id ? `#${el.id}` : (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
        out.push({ selector: el.tagName.toLowerCase() + id, visibleHeight: el.clientHeight, contentHeight: el.scrollHeight });
      }
      return out;
    });
    if (opts.expandScrollers && r.scrollContainers.length) {
      // Let inner scroll areas grow so the full-page capture includes their content.
      await page.evaluate(() => {
        for (const el of document.querySelectorAll('body *')) {
          const cs = getComputedStyle(el);
          if (/(auto|scroll)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 40 && el.clientHeight >= 150) {
            el.style.setProperty('height', 'auto', 'important');
            el.style.setProperty('max-height', 'none', 'important');
            el.style.setProperty('overflow', 'visible', 'important');
          }
        }
        for (let n of [document.documentElement, document.body]) {
          n.style.setProperty('height', 'auto', 'important');
          n.style.setProperty('overflow', 'visible', 'important');
        }
      });
      r.scrollersExpanded = true;
    }
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
    // Keyboard probe last: it moves focus and scrolls, which would change the screenshot.
    if (opts.keyboard && !vp.isMobile) {
      try { r.keyboard = await keyboardProbe(page, opts.maxTabStops); } catch (e) { r.keyboardError = String(e.message).slice(0, 200); }
      try {
        await page.evaluate(() => document.querySelectorAll('input[type=password]').forEach((i) => { i.value = ''; }));
        const tree = await page.locator('body').ariaSnapshot({ timeout: 10000 });
        const dir = path.join(opts.out, slug(pagePath));
        fs.mkdirSync(dir, { recursive: true });
        r.ariaSnapshot = path.join(dir, `${opts.label}__${vp.name}__${theme}${opts.locale ? `__${opts.locale}` : ''}.aria.yml`);
        fs.writeFileSync(r.ariaSnapshot, tree);
      } catch (e) { r.ariaSnapshotError = String(e.message).slice(0, 200); }
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
    a11ySerious: r.a11y?.filter((v) => v.impact === 'critical' || v.impact === 'serious').length ?? 0,
    a11yOther: r.a11y?.filter((v) => v.impact !== 'critical' && v.impact !== 'serious').length ?? 0,
    a11yScanned: Boolean(r.a11y),
    smallTargets: r.smallTargets?.length ?? 0,
    scrollContainers: r.scrollersExpanded ? 0 : (r.scrollContainers?.length ?? 0),
    hydration: r.consoleErrors.filter((e) => e.kind === 'hydration-mismatch').length,
    structure: r.structure?.issues?.length ?? 0,
    cls: r.timing?.cls ?? null,
    keyboardNoIndicator: r.keyboard?.noIndicator.length ?? 0,
    keyboardTrap: Boolean(r.keyboard?.trap),
    pointerOnly: r.keyboard?.pointerOnly.length ?? 0,
    tokensInStorage: [...(r.storage?.localStorage || []), ...(r.storage?.sessionStorage || [])].filter((x) => x.flags.length).length,
    apiCalls: calls.length,
    slowestApiMs: r.api.slowest[0]?.ms ?? null,
    redirectedToLogin: r.redirectedToLogin || false,
    // Without width=device-width, phones lay the page out at 980px and shrink it.
    missingViewportMeta: vp.isMobile && r.navigationError === undefined && !/width\s*=\s*device-width/.test(r.viewportMeta || ''),
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
    axe: args.axe !== false,
    idleTimeout: Number(args['idle-timeout'] ?? 5000),
    expandScrollers: args['expand-scrollers'] === true,
    keyboard: args.keyboard === true,
    maxTabStops: Number(args['max-tab-stops'] ?? 80),
  };
  opts.axeSource = opts.axe ? loadAxeSource() : null;
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
          const n = s.pageErrors + s.consoleErrors + s.failedRequests + s.httpErrors + s.rawKeys + s.badText + s.lowContrast + s.brokenImages + s.tokensInStorage
            + s.a11ySerious + s.smallTargets + s.structure + s.keyboardNoIndicator + s.pointerOnly
            + (s.overflow ? 1 : 0) + (s.navigationError ? 1 : 0) + (s.redirectedToLogin ? 1 : 0) + (s.missingViewportMeta ? 1 : 0) + (s.keyboardTrap ? 1 : 0) + ((s.cls ?? 0) > 0.1 ? 1 : 0);
          actionable += n;
          console.log(
            `[${opts.label} ${vp.name} ${theme}${opts.locale ? ' ' + opts.locale : ''}] ${p} -> ${s.status ?? 'ERR'}` +
            `${r.redirected ? ` (redirected to ${new URL(r.finalUrl).pathname})` : ''}` +
            ` | pageErrors ${s.pageErrors} console ${s.consoleErrors} failed ${s.failedRequests}(+${s.failedNoise} noise)` +
            ` http>=400 ${s.httpErrors} rawKeys ${s.rawKeys} badText ${s.badText} overflow ${s.overflow ? 'YES' : 'no'}` +
            ` lowContrast ${s.lowContrast} brokenImg ${s.brokenImages} tokensInStorage ${s.tokensInStorage}` +
            ` a11y ${s.a11yScanned ? `${s.a11ySerious} serious/critical +${s.a11yOther}` : 'SKIPPED'} structure ${s.structure}` +
            `${vp.hasTouch ? ` smallTargets ${s.smallTargets}` : ''}` +
            `${s.scrollContainers ? ` INNER-SCROLL x${s.scrollContainers} (capture misses content; re-run with --expand-scrollers)` : ''}` +
            `${s.hydration ? ` HYDRATION x${s.hydration}` : ''}` +
            `${s.cls != null ? ` cls ${s.cls}${s.cls > 0.1 ? ' (POOR)' : ''}` : ''}` +
            `${r.keyboard ? ` | keyboard stops ${r.keyboard.stops} noFocusRing ${s.keyboardNoIndicator} pointerOnly ${s.pointerOnly}${s.keyboardTrap ? ` TRAP at ${r.keyboard.trap}` : ''}` : ''}` +
            `${s.missingViewportMeta ? ' MISSING <meta name=viewport width=device-width>' : ''}` +
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

// -------------------------------------------------------------- discover
// Breadth-first link crawl that finds pages the inventory missed (SPA routes, query-string
// tabs, links built at runtime). Safety: GET navigations only, no clicks, no form submits,
// and links that look like they change state (logout, delete...) are never followed.

const DEFAULT_DENY = '(log-?out|sign-?out|log-?off|delete|destroy|remove|unsubscribe|deactivate|revoke|disconnect|cancel|reset|impersonat|switch-?(account|user)|/api/)';
const KEEP_QUERY = /^(tab|step|view|section|mode|panel|type|filter)$/i;
const SKIP_EXT = /\.(pdf|zip|gz|csv|xlsx?|docx?|pptx?|png|jpe?g|gif|svg|webp|ico|mp4|webm|mp3|wav|json|xml|txt|ics)$/i;

// Collapse ids so /players/42 and /players/43 count as one page, which stops endless crawls.
function routePattern(u) {
  const segs = u.pathname.split('/').map((seg) => {
    if (/^\d+$/.test(seg)) return ':id';
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(seg)) return ':uuid';
    if (/^[0-9a-f]{24}$/i.test(seg)) return ':id';
    if (seg.length >= 16 && /\d/.test(seg) && /^[\w-]+$/.test(seg)) return ':token';
    return seg;
  });
  const q = [...u.searchParams.entries()].filter(([k]) => KEEP_QUERY.test(k)).sort(([a], [b]) => a.localeCompare(b));
  return segs.join('/') + (q.length ? '?' + q.map(([k, v]) => `${k}=${v}`).join('&') : '');
}

// /players/[id], /players/:id, /players/<int:pk>, /players/{id} and /blog/[...slug] as regexes.
function trackedMatchers(progressFile) {
  const state = JSON.parse(fs.readFileSync(progressFile, 'utf8'));
  return state.pages.flatMap((p) => [p.inventory_route || p.route.replace(/ \[.*\]$/, ''), p.url].filter(Boolean)).map((r) => {
    const [pathPart, query] = r.split('?');
    const esc = pathPart.replace(/\/$/, '').split('/').map((seg) => {
      if (/^\[\[?\.\.\./.test(seg) || seg === '*' || seg === '**') return '.*';
      if (/^\[.*\]$|^:|^<.*>$|^\{.*\}$|^\$/.test(seg)) return '[^/]+';
      if (/^\(.*\)$/.test(seg)) return null; // Next.js route group: not in the URL
      return seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }).filter((x) => x !== null).join('/');
    return { route: r, re: new RegExp(`^${esc || ''}/?$`), query: query || null };
  });
}

// URLs listed in robots.txt sitemaps or /sitemap.xml (one level of sitemap index followed).
async function sitemapUrls(context, origin) {
  const get = async (u) => {
    try { const r = await context.request.get(u, { timeout: 8000 }); return r.ok() ? await r.text() : ''; } catch { return ''; }
  };
  const robots = await get(`${origin}/robots.txt`);
  const toLocal = (u) => { try { const x = new URL(u, origin); return new URL(x.pathname + x.search, origin).toString(); } catch { return null; } };
  const maps = [...robots.matchAll(/^\s*sitemap:\s*(\S+)/gim)].map((m) => toLocal(m[1])).filter(Boolean);
  if (!maps.length) maps.push(`${origin}/sitemap.xml`);
  const urls = new Set();
  for (const m of maps.slice(0, 5)) {
    const xml = await get(m);
    const locs = [...xml.matchAll(/<loc>\s*([^<\s]+)\s*<\/loc>/g)].map((x) => x[1]);
    if (/<sitemapindex/i.test(xml)) {
      for (const child of locs.slice(0, 10)) for (const x of (await get(toLocal(child))).matchAll(/<loc>\s*([^<\s]+)\s*<\/loc>/g)) urls.add(x[1]);
    } else locs.forEach((l) => urls.add(l));
  }
  // Local copies often ship a sitemap that names the production host: keep the path, use ours.
  const local = new Set();
  for (const u of urls) { try { const x = new URL(u); local.add(new URL(x.pathname + x.search, origin).toString()); } catch { /* bad loc */ } }
  return [...local].slice(0, 2000);
}

async function discover(args) {
  const base = args.base || process.env.WEBSITE_AUDIT_BASE;
  if (!base) die('--base is required');
  if (args.storage && !fs.existsSync(args.storage)) die(`storage state not found: ${args.storage}`);
  const origin = new URL(base).origin;
  const deny = new RegExp(args.deny ? `${DEFAULT_DENY}|${args.deny}` : DEFAULT_DENY, 'i');
  const maxPages = Number(args['max-pages'] ?? 150);
  const timeout = Number(args.timeout ?? 20000);
  const label = args.label || (args.storage ? path.basename(args.storage).replace(/\.json$/, '') : 'anon');
  const out = args.out || '.website-audit/discover';

  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ storageState: args.storage || undefined, viewport: { width: 1440, height: 900 } });
  // Belt and braces: refuse any non-GET document navigation the page itself tries.
  await context.route('**/*', (route) => {
    const req = route.request();
    if (req.isNavigationRequest() && req.method() !== 'GET') return route.abort();
    return route.continue();
  });
  const page = await context.newPage();
  const queue = (args.path?.length ? args.path : ['/']).map((p) => ({ url: new URL(p, base).toString(), from: '(start)' }));
  if (args.sitemap !== false) {
    for (const u of await sitemapUrls(context, origin)) queue.push({ url: u, from: '(sitemap)' });
  }
  const seen = new Set();
  const pages = [];
  const skipped = new Map();
  let sessionLost = null;
  const startUrl = queue[0].url;
  let previous = '(start)';
  try {
    while (queue.length && pages.length < maxPages) {
      const { url, from } = queue.shift();
      const u = new URL(url);
      const pattern = routePattern(u);
      if (seen.has(pattern)) continue;
      if (from !== '(start)' && SKIP_EXT.test(u.pathname)) continue;
      if (from !== '(start)' && deny.test(u.pathname + u.search)) {
        skipped.set(u.pathname + u.search, 'deny-list (could change state)');
        continue;
      }
      seen.add(pattern);
      const item = { pattern, sample: u.pathname + u.search, from };
      try {
        const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
        await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
        item.status = resp ? resp.status() : null;
        const finalPath = new URL(page.url()).pathname;
        if (finalPath !== u.pathname) item.redirectedTo = finalPath;
        if (args.storage && item.redirectedTo && /log-?in|sign-?in|auth/i.test(finalPath)) {
          item.redirectedToLogin = true;
          // Gated page, or did the session die? The start page answered before; ask it again.
          await page.goto(startUrl, { waitUntil: 'domcontentloaded', timeout }).catch(() => {});
          await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
          if (/log-?in|sign-?in|auth/i.test(new URL(page.url()).pathname)) sessionLost = `after visiting ${previous}`;
          await page.goto(url, { waitUntil: 'domcontentloaded', timeout }).catch(() => {});
        }
        const links = await page.evaluate(() => [...document.querySelectorAll('a[href]')].map((a) => a.href));
        for (const href of links) {
          let l;
          try { l = new URL(href); } catch { continue; }
          if (l.origin !== origin || SKIP_EXT.test(l.pathname)) continue;
          l.hash = '';
          const where = l.pathname + l.search;
          if (deny.test(where)) { skipped.set(where, 'deny-list (could change state)'); continue; }
          if (!seen.has(routePattern(l))) queue.push({ url: l.toString(), from: item.sample });
        }
      } catch (e) {
        item.error = String(e.message).split('\n')[0].slice(0, 200);
      }
      pages.push(item);
      previous = item.sample;
      console.log(`${item.status ?? 'ERR'} ${item.pattern}${item.redirectedTo ? ` -> ${item.redirectedTo}` : ''}${item.error ? ` (${item.error})` : ''}`);
      if (sessionLost) break;
    }
  } finally {
    await browser.close();
  }

  const report = { base, label, at: new Date().toISOString(), maxPages, truncated: queue.length > 0 && pages.length >= maxPages, sessionLost, pages, skipped: [...skipped].map(([p, why]) => ({ path: p, why })) };
  if (args.compare) {
    const matchers = trackedMatchers(args.compare);
    report.untracked = pages.filter((p) => !p.error && !p.redirectedToLogin && (p.status ?? 0) < 400).filter((p) => {
      const [pp, q] = p.sample.split('?');
      const pathHit = matchers.filter((m) => m.re.test(pp.replace(/\/$/, '') || '/'));
      if (!pathHit.length) return true;
      // A kept query param (?tab=billing) is its own page unless a tracked URL names it.
      const kept = p.pattern.includes('?');
      return kept && !pathHit.some((m) => m.query && q && q.includes(m.query.split('&')[0]));
    }).map((p) => p.sample);
  }
  fs.mkdirSync(out, { recursive: true });
  const file = path.join(out, `${label}.json`);
  fs.writeFileSync(file, JSON.stringify(report, null, 2));
  console.log(`\n${pages.length} page pattern(s) visited as ${label}; ${skipped.size} link(s) skipped by the deny list; report: ${file}`);
  if (report.truncated) console.log(`STOPPED at --max-pages ${maxPages} with ${queue.length} link(s) still queued; raise it to go further.`);
  if (sessionLost) console.log(`SESSION LOST ${sessionLost}: a followed link probably signed the session out. Add it to --deny, log in again, and re-run.`);
  if (report.untracked) {
    console.log(report.untracked.length ? `\nNot in the progress file (add each that renders distinct UI with progress.py add):` : '\nEvery discovered page is already tracked.');
    for (const s of report.untracked) console.log(`  NEW ${s}`);
  }
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
const run = { capture, discover, login, selftest }[cmd];
if (!run) die(`unknown command "${cmd}" (capture | discover | login | selftest)`);
run(args).catch((e) => die(e.stack || String(e), 1));

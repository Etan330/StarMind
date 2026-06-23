// Bilibili favorites page extractor
// Injected via CDP Runtime.evaluate
(() => {
  const validUrl = (raw) => {
    try {
      const url = new URL(raw, location.href);
      if (!/(^|\.)bilibili\.com$/.test(url.hostname)) return null;
      if (!/\/video\/BV[a-zA-Z0-9]+/.test(url.pathname)) return null;
      return url.href;
    } catch (_) { return null; }
  };

  const extractBvid = (href) => {
    const m = href.match(/\/video\/(BV[a-zA-Z0-9]+)/);
    return m ? m[1] : null;
  };

  const anchors = Array.from(document.querySelectorAll('a[href]'));
  const seen = new Set();
  const items = [];

  for (const a of anchors) {
    const href = validUrl(a.getAttribute('href'));
    if (!href) continue;
    const bvid = extractBvid(href);
    if (!bvid || seen.has(bvid)) continue;
    seen.add(bvid);

    const container = a.closest('li, .fav-video-list, [class*="video-item"], [class*="card"]') || a;
    const title = (a.getAttribute('title') || a.innerText || '').trim().slice(0, 180);
    const authorEl = container.querySelector('[class*="author"], [class*="up-name"], .fav-author');
    const author = authorEl ? authorEl.innerText.trim() : null;

    items.push({ url: href, title, author, bvid });
  }

  return JSON.stringify(items);
})()

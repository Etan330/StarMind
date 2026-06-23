// Douyin favorites page extractor
// Injected via CDP Runtime.evaluate
(() => {
  const validUrl = (raw) => {
    try {
      const url = new URL(raw, location.href);
      if (!/(^|\.)douyin\.com$|(^|\.)iesdouyin\.com$/.test(url.hostname)) return null;
      if (!/(\/video\/|\/note\/|\/share\/video\/|\/share\/note\/)/.test(url.pathname)) return null;
      return url.href;
    } catch (_) { return null; }
  };

  const anchors = Array.from(document.querySelectorAll('a[href]'));
  const seen = new Set();
  const items = [];

  for (const a of anchors) {
    const href = validUrl(a.getAttribute('href'));
    if (!href) continue;
    const key = href.replace(/[?#].*$/, '');
    if (seen.has(key)) continue;
    seen.add(key);

    const container = a.closest('li, article, [class*="card"], [class*="Card"], [class*="item"], [class*="Item"]') || a;
    const text = (container.innerText || '').trim().split('\n').filter(l => l.trim().length > 3);
    const title = (a.getAttribute('title') || a.getAttribute('aria-label') || text[0] || '').slice(0, 180);
    const kind = /\/note\//.test(href) ? 'note' : 'video';

    items.push({ url: href, title, author: null, kind });
  }

  return JSON.stringify(items);
})()

// Xiaohongshu (Little Red Book) favorites page extractor
// Injected via CDP Runtime.evaluate
(() => {
  const validUrl = (raw) => {
    try {
      const url = new URL(raw, location.href);
      if (!/(^|\.)xiaohongshu\.com$|(^|\.)xhslink\.com$/.test(url.hostname)) return null;
      if (!/\/(explore|discovery\/item)\/[a-f0-9]+/.test(url.pathname)) return null;
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

    const container = a.closest('[class*="note-item"], [class*="card"], section, li') || a;
    const title = (a.getAttribute('title') || a.innerText || '').trim().split('\n')[0].slice(0, 180);
    const authorEl = container.querySelector('[class*="author"], [class*="name"]');
    const author = authorEl ? authorEl.innerText.trim() : null;

    items.push({ url: href, title, author });
  }

  return JSON.stringify(items);
})()

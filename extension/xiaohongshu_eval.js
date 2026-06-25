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

  const cleanText = (value) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 180);

  const isBadTitle = (text, href = '') => {
    const value = cleanText(text);
    if (!value) return true;
    if (value === href) return true;
    if (/^https?:\/\//i.test(value)) return true;
    if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(value)) return true;
    if (/^[\d\s.,，万wW:+:-]+$/.test(value)) return true;
    if (/播放|弹幕|点赞|收藏|评论|分享|观看|浏览/.test(value) && value.length < 30) return true;
    return false;
  };

  const titleScore = (text, href = '') => {
    const value = cleanText(text);
    if (isBadTitle(value, href)) return 0;
    let score = 1;
    if (/[\u4e00-\u9fffA-Za-z]/.test(value)) score += 2;
    if (value.length >= 6) score += 2;
    if (value.length >= 14) score += 1;
    if (/播放|弹幕|点赞|收藏|评论|分享|观看|浏览/.test(value)) score -= 2;
    return Math.max(0, score);
  };

  const collectTexts = (node, href) => {
    const texts = [];
    if (!node) return texts;
    const add = (value) => {
      const text = cleanText(value);
      if (!isBadTitle(text, href)) texts.push(text);
    };
    add(node.getAttribute && node.getAttribute('title'));
    add(node.getAttribute && node.getAttribute('aria-label'));
    add(node.innerText);
    return texts;
  };

  const candidateSelectors = [
    '[title]',
    '[class*="title"]',
    '[class*="desc"]',
    '[class*="content"]',
    '.title',
    '.desc',
    '.note-title',
    '.footer .title',
  ];

  const anchors = Array.from(document.querySelectorAll('a[href]'));
  const byKey = new Map();

  for (const a of anchors) {
    const href = validUrl(a.getAttribute('href'));
    if (!href) continue;
    const key = href.replace(/[?#].*$/, '');
    const container = a.closest('[class*="note-item"], [class*="card"], section, li') || a;
    const candidates = [...collectTexts(a, href)];
    for (const node of Array.from(container.querySelectorAll(candidateSelectors.join(',')))) {
      candidates.push(...collectTexts(node, href));
    }

    let bestTitle = '';
    let bestScore = 0;
    for (const text of candidates) {
      const score = titleScore(text, href);
      if (score > bestScore) {
        bestTitle = text;
        bestScore = score;
      }
    }

    const authorEl = container.querySelector('[class*="author"], [class*="name"]');
    const author = authorEl ? cleanText(authorEl.innerText) : null;
    const existing = byKey.get(key);
    if (!existing || bestScore > existing.score) {
      byKey.set(key, { url: href, title: bestTitle, author, score: bestScore });
    }
  }

  return JSON.stringify(Array.from(byKey.values()).map(({ score, ...item }) => item));
})()

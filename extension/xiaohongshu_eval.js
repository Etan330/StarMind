// Xiaohongshu (Little Red Book) favorites page extractor
// Injected via CDP Runtime.evaluate
(() => {
  const NOTE_ID_RE = /^[a-f0-9]{12,}$/i;

  const extractNoteId = (url) => {
    const parts = url.pathname.split('/').filter(Boolean);
    if (parts[0] === 'explore' && NOTE_ID_RE.test(parts[1] || '')) return parts[1];
    if (parts[0] === 'discovery' && parts[1] === 'item' && NOTE_ID_RE.test(parts[2] || '')) return parts[2];
    if (parts[0] === 'user' && parts[1] === 'profile' && parts.length >= 4 && NOTE_ID_RE.test(parts[3] || '')) return parts[3];
    return '';
  };

  const buildShareUrl = (noteId, sourceUrl) => {
    if (!noteId) return '';
    const shareUrl = new URL(`https://www.xiaohongshu.com/discovery/item/${noteId}`);
    shareUrl.searchParams.set('source', 'webshare');
    shareUrl.searchParams.set('xhsshare', 'pc_web');
    const token = sourceUrl.searchParams.get('xsec_token');
    if (token) shareUrl.searchParams.set('xsec_token', token);
    shareUrl.searchParams.set('xsec_source', 'pc_share');
    return shareUrl.href;
  };

  const validUrl = (raw) => {
    try {
      const url = new URL(raw, location.href);
      if (!/(^|\.)xiaohongshu\.com$|(^|\.)xhslink\.com$/.test(url.hostname)) return null;
      const noteId = extractNoteId(url);
      if (!noteId) return null;
      return { href: url.href, noteId, xsecToken: url.searchParams.get('xsec_token') || '', shareUrl: buildShareUrl(noteId, url) };
    } catch (_) { return null; }
  };

  const cleanText = (value) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 180);

  const isBadTitle = (text, href = '') => {
    const value = cleanText(text);
    if (!value) return true;
    if (value === href) return true;
    if (/^https?:\/\//i.test(value)) return true;
    if (value.length < 2) return true;
    if (/^\[[^\]]{0,3}$/.test(value)) return true;
    if (/^[#@]?$/.test(value)) return true;
    if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(value)) return true;
    if (/^[\d\s.,，万wW:+:-]+$/.test(value)) return true;
    if (/^(我|我的|首页|发现|消息|通知|登录|注册|搜索|发布|购物|直播|更多|展开|收起|打开|关闭|关注|已关注|小红书|xiaohongshu)$/i.test(value)) return true;
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

  const buildShareText = (title, shareUrl) => {
    if (!shareUrl) return '';
    const prefix = title ? `【${title} | 小红书 - 你的生活兴趣社区】 ` : '';
    return `${prefix}${shareUrl}`;
  };

  // 尽力从卡片容器抓发布时间：先 <time> 的 datetime/innerText，再正则匹配常见文案；抓不到留空。
  const DATE_PATTERN = /(\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}|\d{1,2}[-/月.]\d{1,2}日?|\d+\s*(天|小时|分钟|周|个月|月|年)前|昨天|前天|今天)/;
  const findPublishTime = (container) => {
    if (!container) return '';
    const timeEl = container.querySelector && container.querySelector('time');
    if (timeEl) {
      const dt = cleanText(timeEl.getAttribute('datetime') || timeEl.innerText);
      if (dt) return dt.slice(0, 40);
    }
    const dateNode = container.querySelector && container.querySelector('[class*="date"], [class*="Date"], [class*="time"], [class*="Time"]');
    if (dateNode) {
      const matched = cleanText(dateNode.innerText).match(DATE_PATTERN);
      if (matched) return matched[0].slice(0, 40);
    }
    const text = container.innerText ? String(container.innerText) : '';
    const matched = text.match(DATE_PATTERN);
    return matched ? matched[0].slice(0, 40) : '';
  };

  const candidateSelectors = [
    '[title]',
    '[class*="title"]',
    '[class*="Title"]',
    '[class*="desc"]',
    '[class*="Desc"]',
    '.title',
    '.desc',
    '.note-title',
    '.footer .title',
  ];

  const containerSelectors = [
    '[class*="note-item"]',
    '[class*="NoteItem"]',
    '[class*="note-card"]',
    '[class*="NoteCard"]',
    '[class*="feeds-page"] section',
    'section[class*="note"]',
    'li[class*="note"]',
    '[class*="card"]',
  ];

  const chooseBetter = (current, incoming) => {
    if (!current) return incoming;
    if (incoming.score !== current.score) return incoming.score > current.score ? incoming : current;
    if (incoming.title && !current.title) return incoming;
    if (incoming.share_text && !current.share_text) return incoming;
    if (incoming.url.includes('xsec_source=pc_collect') && !current.url.includes('xsec_source=pc_collect')) return incoming;
    return current;
  };

  const anchors = Array.from(document.querySelectorAll('a[href]'));
  const byKey = new Map();

  for (const a of anchors) {
    const urlInfo = validUrl(a.getAttribute('href'));
    if (!urlInfo) continue;
    const href = urlInfo.href;
    const container = a.closest(containerSelectors.join(', ')) || a;
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
    if (!bestTitle || bestScore <= 0) continue;

    const authorEl = container.querySelector('[class*="author"], [class*="Author"], [class*="name"], [class*="Name"]');
    const author = authorEl ? cleanText(authorEl.innerText) : null;
    const item = {
      url: href,
      title: bestTitle,
      author,
      note_id: urlInfo.noteId,
      xsec_token: urlInfo.xsecToken,
      share_url: urlInfo.shareUrl,
      share_text: buildShareText(bestTitle, urlInfo.shareUrl),
      publish_time: findPublishTime(container),
      score: bestScore,
    };
    byKey.set(urlInfo.noteId, chooseBetter(byKey.get(urlInfo.noteId), item));
  }

  return JSON.stringify(Array.from(byKey.values()).map(({ score, ...item }) => item));
})()

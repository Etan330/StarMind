# Lint Wiki

检查 Wiki 健康度，只输出 JSON：

```json
{
  "orphan_pages": [],
  "duplicate_topics": [],
  "missing_source_refs": [],
  "dead_links": [],
  "stale_claims": [],
  "conflicts": [],
  "low_risk_fixes": [],
  "high_risk_review_items": []
}
```

MVP 阶段只生成报告，不自动修复。


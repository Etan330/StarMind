## [ERR-20260630-001] pytest_import_path

**Logged**: 2026-06-30T00:00:00Z
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
Running a single pytest test without `PYTHONPATH=.` could not import the local `app` package.

### Error
```text
ModuleNotFoundError: No module named 'app'
```

### Context
- Command attempted: `pytest tests/test_model_profiles.py::test_settings_page_shows_uuap_redirect_diagnostic -q`
- Working directory: `/Users/sunpeng330/Desktop/star/StarMind`
- Retrying with `PYTHONPATH=.` collected and ran the test.

### Suggested Fix
Use `PYTHONPATH=.` for targeted pytest runs in this project unless packaging/test config is updated to add the repo root automatically.

### Metadata
- Reproducible: yes
- Related Files: `tests/test_model_profiles.py`

---

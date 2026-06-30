(function () {
  const openModal = (modal) => {
    if (!modal) return
    modal.hidden = false
    document.body.classList.add("modal-open")
    const focusTarget = modal.querySelector("input, textarea, select, button")
    window.setTimeout(() => focusTarget?.focus(), 40)
  }

  const closeModal = (modal) => {
    if (!modal) return
    modal.hidden = true
    document.body.classList.remove("modal-open")
  }

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-open-modal]")
    if (opener) {
      event.preventDefault()
      openModal(document.querySelector(opener.dataset.openModal))
      return
    }

    const closer = event.target.closest("[data-close-modal]")
    if (closer) {
      event.preventDefault()
      closeModal(closer.closest(".modal-shell"))
      return
    }

    const modal = event.target.classList.contains("modal-shell") ? event.target : null
    if (modal) closeModal(modal)
  })

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return
    document.querySelectorAll(".modal-shell:not([hidden])").forEach(closeModal)
  })

  document.querySelectorAll("[data-provider-picker]").forEach((picker) => {
    const data = JSON.parse(picker.dataset.providerConfigs || "{}")
    const select = picker.querySelector("select")
    const name = picker.querySelector("[data-provider-name]")
    const key = picker.querySelector("[data-provider-key]")
    const form = picker.closest("form")
    const urlInput = form?.querySelector("[data-provider-url-input]")
    const modelInput = form?.querySelector("[data-model-input]")

    const updateModelOptions = (provider, keepCurrent) => {
      if (!modelInput) return
      const models = Array.isArray(provider.models) ? provider.models : []
      if (modelInput.tagName !== "SELECT") {
        if (!modelInput.value && models.length) {
          modelInput.placeholder = models[0]
        }
        return
      }

      const current = keepCurrent ? modelInput.dataset.currentModel || modelInput.value : ""
      const selected = current && models.includes(current) ? current : models[0] || current
      const options = selected && !models.includes(selected) ? [selected, ...models] : models
      modelInput.innerHTML = ""
      options.forEach((model) => {
        const option = document.createElement("option")
        option.value = model
        option.textContent = model
        option.selected = model === selected
        modelInput.appendChild(option)
      })
      if (selected) {
        modelInput.value = selected
        modelInput.dataset.currentModel = selected
      }
    }

    const update = (keepCurrent = true) => {
      const provider = data[select.value] || {}
      if (name) name.textContent = provider.display_name || select.value
      if (urlInput) urlInput.value = provider.base_url || ""
      if (key) key.textContent = provider.api_key_label || "API Key"
      updateModelOptions(provider, keepCurrent)
    }

    select?.addEventListener("change", () => update(false))
    update()
  })

  document.querySelectorAll("[data-pending-tabs]").forEach((tabs) => {
    const buttons = Array.from(tabs.querySelectorAll("[data-tab-target]"))
    const card = tabs.closest(".item-list-card") || document
    const panels = Array.from(card.querySelectorAll("[data-tab-panel]"))
    const preview = document.querySelector(".preview-card")
    const previewTitle = preview?.querySelector("[data-preview-title]")
    const previewMeta = preview?.querySelector("[data-preview-meta]")
    const previewUrl = preview?.querySelector("[data-preview-url]")
    const previewSource = preview?.querySelector("[data-preview-source]")
    const actionGroups = {
      candidate: preview?.querySelector("[data-actions-candidate]"),
      source: preview?.querySelector("[data-actions-source]"),
      recycled: preview?.querySelector("[data-actions-recycled]"),
      empty: preview?.querySelector("[data-actions-empty]"),
    }

    const setActions = (type, id) => {
      Object.entries(actionGroups).forEach(([key, element]) => {
        if (!element) return
        element.hidden = key !== type
      })
      if (type === "candidate") {
        preview?.querySelector("[data-candidate-action='confirm']")?.setAttribute("action", `/candidates/${id}/confirm`)
        preview?.querySelector("[data-candidate-action='skip']")?.setAttribute("action", `/candidates/${id}/skip`)
        preview?.querySelector("[data-candidate-action='recycle']")?.setAttribute("action", `/candidates/${id}/recycle`)
      }
      if (type === "source") {
        preview?.querySelectorAll("[data-source-action]").forEach((form) => {
          form.setAttribute("action", `/agent/raw-sources/${id}/create-page`)
        })
      }
      if (type === "recycled") {
        preview?.querySelectorAll("[data-recycled-action]").forEach((form) => {
          form.setAttribute("action", `/recycle/${id}/restore`)
        })
      }
    }

    const updatePreview = (item, fallback = {}) => {
      if (!item || !preview) {
        if (previewTitle) previewTitle.textContent = fallback.title || "这里暂无内容"
        if (previewMeta) previewMeta.textContent = fallback.meta || ""
        if (previewUrl) previewUrl.textContent = fallback.url || "切换其他分类，或回到首页同步新的收藏。"
        if (previewSource) previewSource.textContent = fallback.source || "空列表"
        setActions("empty")
        return
      }
      const type = item.dataset.previewType || "empty"
      const id = item.dataset.previewId || "0"
      if (previewTitle) previewTitle.textContent = item.dataset.itemTitle || item.dataset.previewTitle || "选择一条内容查看预览"
      if (previewMeta) previewMeta.textContent = item.dataset.itemMeta || item.dataset.previewMeta || ""
      if (previewUrl) previewUrl.textContent = item.dataset.itemUrl || item.dataset.previewUrl || ""
      if (previewSource) previewSource.textContent = item.dataset.itemStatus || item.dataset.previewStatus || "预览"
      setActions(type === "candidate" ? "candidate" : type === "source" ? "source" : type === "recycled" ? "recycled" : "empty", id)
    }

    const activate = (target) => {
      buttons.forEach((button) => {
        const active = button.dataset.tabTarget === target
        button.classList.toggle("active", active)
        button.setAttribute("aria-selected", active ? "true" : "false")
      })
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.tabPanel !== target
      })
      const activePanel = panels.find((panel) => panel.dataset.tabPanel === target)
      const firstItem = activePanel?.querySelector(".content-item")
      panels.forEach((panel) => {
        panel.querySelectorAll(".content-item").forEach((item) => item.classList.remove("active"))
      })
      firstItem?.classList.add("active")
      const targetButton = buttons.find((button) => button.dataset.tabTarget === target)
      updatePreview(firstItem, {
        title: targetButton?.childNodes?.[0]?.textContent?.trim() || "这里暂无内容",
        source: targetButton?.childNodes?.[0]?.textContent?.trim() || "空列表",
        url: activePanel?.innerText?.trim() || "这个分类暂时没有内容。",
      })
    }

    tabs.addEventListener("click", (event) => {
      const button = event.target.closest("[data-tab-target]")
      if (!button) return
      activate(button.dataset.tabTarget)
    })

    panels.forEach((panel) => {
      panel.addEventListener("click", (event) => {
        const item = event.target.closest(".content-item")
        if (!item) return
        panel.querySelectorAll(".content-item").forEach((current) => current.classList.remove("active"))
        item.classList.add("active")
        updatePreview(item)
      })
    })

    const initialButton = buttons.find((button) => button.classList.contains("active")) || buttons[0]
    activate(initialButton?.dataset.tabTarget || "pending")
  })

  document.querySelectorAll("[data-create-tabs]").forEach((tabs) => {
    const buttons = Array.from(tabs.querySelectorAll("[data-tab-target]"))
    const container = tabs.closest(".create-task-panel") || document
    const panels = Array.from(container.querySelectorAll("[data-tab-panel]"))
    const activate = (target) => {
      buttons.forEach((button) => {
        const active = button.dataset.tabTarget === target
        button.classList.toggle("active", active)
        button.setAttribute("aria-selected", active ? "true" : "false")
      })
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.tabPanel !== target
      })
    }
    tabs.addEventListener("click", (event) => {
      const button = event.target.closest("[data-tab-target]")
      if (!button) return
      activate(button.dataset.tabTarget)
    })
    const initialButton = buttons.find((button) => button.classList.contains("active")) || buttons[0]
    activate(initialButton?.dataset.tabTarget || "link")
  })

  const v3Placeholders = {
    favorites: "可以先不输入，直接确认本地可见收藏夹同步",
    link: "粘贴一篇文章、视频或网页链接...",
    creator: "粘贴博主主页，或输入账号名称...",
    idea: "写下一个想法、问题、灵感或待整理材料...",
  }

  const v3Track = (eventName, payload = {}) => {
    const body = new URLSearchParams({ event_name: eventName })
    Object.entries(payload).forEach(([key, value]) => {
      if (value !== undefined && value !== null) body.set(key, String(value))
    })
    window.fetch("/events/v3", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
      keepalive: true,
    }).catch(() => {})
  }

  document.querySelectorAll("[data-v3-composer]").forEach((composer) => {
    const input = composer.querySelector("[data-v3-main-input]")
    const modeInput = composer.querySelector("[data-v3-entry-input]")
    const count = composer.querySelector("[data-v3-char-count]")
    const entryCards = Array.from(document.querySelectorAll("[data-v3-entry]"))

    const setMode = (mode, label = "") => {
      if (!modeInput) return
      modeInput.value = mode
      if (input && v3Placeholders[mode]) input.placeholder = v3Placeholders[mode]
      entryCards.forEach((card) => {
        const active = card.dataset.v3Entry === mode
        card.classList.toggle("active", active)
        card.setAttribute("aria-pressed", active ? "true" : "false")
      })
      v3Track("v3_entry_clicked", {
        entry_mode: mode,
        entry: label || mode,
        viewport: window.innerWidth < 640 ? "mobile" : "desktop",
      })
    }

    const updateCount = () => {
      if (count && input) count.textContent = String(input.value.length)
    }

    entryCards.forEach((card) => {
      card.addEventListener("click", () => {
        setMode(card.dataset.v3Entry, card.dataset.v3EntryLabel)
        input?.focus()
      })
    })

    composer.querySelectorAll("[data-v3-entry-shortcut]").forEach((button) => {
      button.addEventListener("click", () => {
        setMode(button.dataset.v3EntryShortcut, button.textContent.trim())
        input?.focus()
      })
    })

    input?.addEventListener("focus", () => {
      if (input.dataset.v3FocusTracked === "true") return
      input.dataset.v3FocusTracked = "true"
      v3Track("v3_primary_input_focused", {
        entry_mode: modeInput?.value || "link",
        viewport: window.innerWidth < 640 ? "mobile" : "desktop",
      })
    })
    input?.addEventListener("input", updateCount)

    const initialMode = modeInput?.value || "link"
    if (v3Placeholders[initialMode] && input) input.placeholder = v3Placeholders[initialMode]
    updateCount()
  })

  document.querySelectorAll("[data-v3-onboarding-dismiss]").forEach((button) => {
    button.addEventListener("click", () => {
      const strip = button.closest("[data-v3-onboarding]")
      if (strip) strip.hidden = true
      v3Track("v3_onboarding_completed", { viewport: window.innerWidth < 640 ? "mobile" : "desktop" })
    })
  })

  document.querySelectorAll("[data-v3-demo-link]").forEach((link) => {
    link.addEventListener("click", () => {
      v3Track("v3_demo_used", { demo_id: new URL(link.href).searchParams.get("demo_id") || "second-brain" })
    })
  })

  function initSourceShell(shell) {
    if (!shell || shell.dataset.bound === "1") return
    shell.dataset.bound = "1"
    const tabs = Array.from(shell.querySelectorAll("[data-collection-tab]"))
    const panels = Array.from(shell.querySelectorAll("[data-source-filter]"))
    const hints = Array.from(shell.querySelectorAll("[data-collection-hint]"))
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const kind = tab.dataset.collectionTab
        tabs.forEach((other) => {
          const active = other === tab
          other.classList.toggle("is-active", active)
          other.setAttribute("aria-selected", active ? "true" : "false")
        })
        panels.forEach((panel) => {
          const match = panel.dataset.collectionKind === kind
          panel.hidden = !match
          panel.classList.toggle("is-hidden", !match)
        })
        hints.forEach((hint) => {
          hint.hidden = hint.dataset.collectionHint !== kind
        })
      })
    })
  }
  document.querySelectorAll("[data-source-shell]").forEach(initSourceShell)

  function initSourceFilter(root) {
    if (!root || root.dataset.bound === "1") return
    root.dataset.bound = "1"
    const platform = root.dataset.platform
    const collectionKind = root.dataset.collectionKind || "history"
    // homepage 输入框在「平台设置」折叠区里，与本面板同属一个 data-source-setup-panel 容器。
    // 用容器作用域查找，避免 /ui/sync 标签栏内联多平台时跨面板串到别的平台输入框（独立页只有一个面板，行为不变）。
    const panelScope = root.closest("[data-source-setup-panel]") || document
    const homepageInput = panelScope.querySelector("[data-source-homepage-input]")
    const limitSelect = root.querySelector("[data-filter-limit]")
    const scanButton = root.querySelector("[data-filter-scan]")
    const classifyButton = root.querySelector("[data-filter-classify]")
    const extractButton = root.querySelector("[data-filter-extract]")
    const resumeButton = root.querySelector("[data-filter-resume]")
    const status = root.querySelector("[data-filter-status]")
    const summary = root.querySelector("[data-filter-summary]")
    const results = root.querySelector("[data-filter-results]")
    const filterToolbar = root.querySelector("[data-filter-toolbar]")
    const usefulnessFilter = root.querySelector("[data-filter-usefulness]")
    const categoryFilter = root.querySelector("[data-filter-category]")
    const timeFilter = root.querySelector("[data-filter-time]")
    const ingestedFilter = root.querySelector("[data-filter-ingested]")
    const saveHistoryButton = root.querySelector("[data-filter-save-history]")
    const rescanHistoryButton = root.querySelector("[data-filter-rescan-history]")
    const isHistory = collectionKind === "history"
    let scannedItems = []
    let classifiedItems = []
    let selectedCandidateIds = []
    let currentJobId = null
    let lastGroups = []
    const resumablePlatforms = new Set(["douyin", "xiaohongshu", "bilibili"])
    // 只有历史 Tab 走 localStorage 续跑：历史是低频「采集一次保存」流程，断点续跑有意义。
    // 新增 Tab 是高频「即采即清」流程——绝不读/写本地缓存，否则陈旧缓存会把历史/旧条目漏渲染出来；
    // 它每次进页面都以 DB（filter_incremental 已去重）为唯一权威源，空就显示空态。
    const canResume = resumablePlatforms.has(platform) && collectionKind === "history"
    const stateKey = `starmind.batchTitleFilter.${platform}.${collectionKind}`

    // 新增 Tab 进页面先把可能残留的旧缓存键清掉（历史代码曾给它写过缓存），杜绝陈旧来源。
    if (collectionKind !== "history") {
      try { window.localStorage?.removeItem(stateKey) } catch (_ignored) {}
    }

    const show = (el, visible) => { if (el) el.hidden = !visible }

    // 历史 Tab 三态布局（gate 在 isHistory）：
    // - 扫描模式：采集数量/扫描/分类/提取可用，藏 保存/重新扫描。
    // - 只读模式（已保存历史）：藏 采集数量/扫描/分类/保存，留 提取(仅勾选)/重新扫描/筛选栏。
    const enterScanMode = () => {
      if (!isHistory) return
      const limitLabel = limitSelect?.closest("label")
      show(limitLabel, true)
      show(scanButton, true)
      show(classifyButton, true)
      show(saveHistoryButton, false)
      show(rescanHistoryButton, false)
    }
    const revealSaveButton = () => {
      if (!isHistory) return
      show(saveHistoryButton, true)
    }
    const enterReadOnlyMode = () => {
      if (!isHistory) return
      const limitLabel = limitSelect?.closest("label")
      show(limitLabel, false)
      show(scanButton, false)
      show(classifyButton, false)
      show(saveHistoryButton, false)
      show(rescanHistoryButton, true)
    }

    const setStatus = (message, tone = "") => {
      if (!status) return
      status.textContent = message
      status.dataset.tone = tone
    }

    const setBusy = (button, busy, text) => {
      if (!button) return
      if (busy) {
        button.dataset.originalText = button.textContent || ""
        button.textContent = text || "处理中..."
        button.disabled = true
      } else {
        button.textContent = button.dataset.originalText || button.textContent || "继续"
        button.disabled = false
      }
    }

    const apiPost = async (url, payload) => {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) {
        const detail = body.detail || body.error
        const message = typeof detail === "object" && detail !== null ? detail.message || JSON.stringify(detail) : detail
        const error = new Error(message || `请求失败：${response.status}`)
        if (typeof detail === "object" && detail !== null) {
          error.code = detail.code || body.code || ""
          error.detail = detail
        }
        throw error
      }
      return body
    }

    const escapeHtml = (value) => String(value || "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]))

    const readSavedState = () => {
      if (!canResume) return null
      try {
        const raw = window.localStorage?.getItem(stateKey)
        if (!raw) return null
        const state = JSON.parse(raw)
        if (!state || state.version !== 1 || state.platform !== platform) {
          window.localStorage?.removeItem(stateKey)
          return null
        }
        return state
      } catch (_error) {
        try { window.localStorage?.removeItem(stateKey) } catch (_ignored) {}
        return null
      }
    }

    const saveState = (stage, extra = {}) => {
      if (!canResume) return
      const state = {
        version: 1,
        platform,
        stage,
        homepageUrl: homepageInput?.value?.trim() || root.dataset.homepageUrl || "",
        limit: limitSelect?.value || "10",
        scannedItems,
        classifiedItems,
        groups: lastGroups,
        selectedCandidateIds,
        summaryText: summary?.textContent || "",
        statusText: status?.textContent || "",
        updatedAt: new Date().toISOString(),
        ...extra,
      }
      try { window.localStorage?.setItem(stateKey, JSON.stringify(state)) } catch (_error) {}
    }

    const clearSavedState = () => {
      if (!canResume) return
      try { window.localStorage?.removeItem(stateKey) } catch (_error) {}
    }

    const renderScannedPreview = (items) => {
      if (!results) return
      results.hidden = false
      if (filterToolbar) filterToolbar.hidden = true
      results.innerHTML = items.map((item) => {
        const extracted = item.extracted === true
        const publishBits = item.published_at ? ` · ${escapeHtml(item.published_at)}` : ""
        const chip = extracted ? '<span class="status-chip success filter-extracted-chip">已入库</span>' : ""
        return `
        <div class="filter-item preview-only${extracted ? " extracted" : ""}">
          <span><strong>${escapeHtml(item.title || item.url)}</strong>${chip}<small>${escapeHtml(item.author || item.platform || "")}${publishBits}</small><a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">打开源网页</a></span>
        </div>
      `}).join("")
    }

    // 把发布时间原文尽力解析为 Date；解析不出返回 null（仅「有发布时间/不限」能命中，避免误判）。
    const parsePublishDate = (raw) => {
      if (!raw) return null
      const text = String(raw).trim()
      const ymd = text.match(/(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})/)
      if (ymd) {
        const d = new Date(Number(ymd[1]), Number(ymd[2]) - 1, Number(ymd[3]))
        return Number.isNaN(d.getTime()) ? null : d
      }
      const rel = text.match(/(\d+)\s*(天|日|周|个?月|小时|分钟|秒)前/)
      if (rel) {
        const n = Number(rel[1])
        const unit = rel[2]
        const now = new Date()
        if (unit.includes("月")) now.setMonth(now.getMonth() - n)
        else if (unit === "周") now.setDate(now.getDate() - n * 7)
        else if (unit === "天" || unit === "日") now.setDate(now.getDate() - n)
        else if (unit === "小时") now.setHours(now.getHours() - n)
        else if (unit === "分钟") now.setMinutes(now.getMinutes() - n)
        else now.setSeconds(now.getSeconds() - n)
        return now
      }
      if (/^(今天|刚刚|昨天|前天)/.test(text)) {
        const now = new Date()
        if (text.startsWith("昨天")) now.setDate(now.getDate() - 1)
        else if (text.startsWith("前天")) now.setDate(now.getDate() - 2)
        return now
      }
      return null
    }

    const itemPassesFilters = (item) => {
      const wantUsefulness = usefulnessFilter?.value || ""
      if (wantUsefulness && item.usefulness !== wantUsefulness) return false
      const wantCategory = categoryFilter?.value || ""
      if (wantCategory && (item.subcategory || "") !== wantCategory) return false
      const wantIngested = ingestedFilter?.value || ""
      if (wantIngested === "ingested" && item.extracted !== true) return false
      if (wantIngested === "not" && item.extracted === true) return false
      const wantTime = timeFilter?.value || ""
      if (wantTime) {
        const date = parsePublishDate(item.published_at)
        if (wantTime === "has") {
          if (!item.published_at) return false
        } else {
          if (!date) return false
          const days = (Date.now() - date.getTime()) / 86400000
          if (days > Number(wantTime)) return false
        }
      }
      return true
    }

    const populateCategoryFilter = () => {
      if (!categoryFilter) return
      const cats = []
      for (const group of lastGroups) {
        const sub = group.subcategory
        if (sub && !cats.includes(sub)) cats.push(sub)
      }
      const current = categoryFilter.value
      categoryFilter.innerHTML = '<option value="">全部类别</option>' + cats.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("")
      if (cats.includes(current)) categoryFilter.value = current
    }

    const renderGroups = (groups) => {
      lastGroups = groups || []
      if (!results) return
      results.hidden = false
      if (filterToolbar) filterToolbar.hidden = lastGroups.length === 0
      populateCategoryFilter()
      applyFilters()
    }

    const applyFilters = () => {
      if (!results) return
      const groups = lastGroups || []
      if (!groups.length) {
        results.innerHTML = '<div class="empty-state">没有可展示的分类结果。</div>'
        return
      }
      let visibleCount = 0
      results.innerHTML = groups.map((group, groupIndex) => {
        const groupKey = `${group.usefulness}-${group.subcategory}-${groupIndex}`
        const items = (group.items || []).filter(itemPassesFilters)
        if (!items.length) return ""
        visibleCount += items.length
        return `
          <section class="filter-group" data-filter-group="${escapeHtml(groupKey)}">
            <div class="filter-group-head">
              <label class="filter-group-check">
                <input type="checkbox" data-group-toggle checked>
                <span>${group.usefulness === "useful" ? "有用" : "没用"} · ${escapeHtml(group.subcategory)}</span>
              </label>
              <span class="status-chip ${group.usefulness === "useful" ? "success" : "warning"}">${items.length} 条</span>
            </div>
            <div class="filter-item-list">
              ${items.map((item) => {
                const extracted = item.extracted === true
                const idx = classifiedItems.indexOf(item)
                const publishBits = item.published_at ? ` · ${escapeHtml(item.published_at)}` : ""
                const chip = extracted ? '<span class="status-chip success filter-extracted-chip">已入库</span>' : ""
                const checked = extracted ? "" : (group.usefulness === "useful" ? "checked" : "")
                const disabled = extracted ? "disabled" : ""
                return `
                <label class="filter-item${extracted ? " extracted" : ""}">
                  <input type="checkbox" data-item-check data-item-index="${idx}" ${checked} ${disabled}>
                  <span>
                    <strong>${escapeHtml(item.title || item.url)}</strong>${chip}
                    <small>${escapeHtml(item.author || item.platform || "未知作者")} · ${escapeHtml(item.reason || "")}${publishBits}</small>
                    <a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">打开源网页</a>
                  </span>
                </label>
              `}).join("")}
            </div>
          </section>
        `
      }).join("")
      if (!visibleCount) {
        results.innerHTML = '<div class="empty-state">当前筛选条件下没有匹配的条目。</div>'
        return
      }
      results.querySelectorAll("[data-group-toggle]").forEach((checkbox) => {
        checkbox.addEventListener("change", () => {
          const group = checkbox.closest("[data-filter-group]")
          group?.querySelectorAll("[data-item-check]:not([disabled])").forEach((itemCheck) => {
            itemCheck.checked = checkbox.checked
          })
        })
      })
    }

    ;[usefulnessFilter, categoryFilter, timeFilter, ingestedFilter].forEach((select) => {
      select?.addEventListener("change", applyFilters)
    })

    const restoreState = () => {
      // 新增 Tab 不做 localStorage 续跑（canResume 已为 false），双保险：直接不恢复，避免陈旧缓存漏渲染。
      if (!isHistory) return
      const saved = readSavedState()
      if (!saved) return
      if (!Array.isArray(saved.scannedItems) || !Array.isArray(saved.classifiedItems) || !Array.isArray(saved.groups) || !Array.isArray(saved.selectedCandidateIds)) {
        clearSavedState()
        return
      }
      scannedItems = saved.scannedItems
      classifiedItems = saved.classifiedItems
      selectedCandidateIds = saved.selectedCandidateIds
      lastGroups = saved.groups
      if (summary && saved.summaryText) {
        summary.hidden = false
        summary.textContent = saved.summaryText
      }
      if (saved.statusText) setStatus(saved.statusText, saved.stage === "completed" ? "ok" : "")
      if (saved.stage === "scanned") {
        renderScannedPreview(scannedItems)
        classifyButton.disabled = scannedItems.length === 0
        extractButton.disabled = true
        if (isHistory) enterScanMode()
      } else if (saved.stage === "classified" || saved.stage === "prepared") {
        renderGroups(lastGroups)
        classifyButton.disabled = scannedItems.length === 0
        extractButton.disabled = classifiedItems.length === 0 && selectedCandidateIds.length === 0
        if (isHistory) { enterScanMode(); if (classifiedItems.length) revealSaveButton() }
      } else if (saved.stage === "completed") {
        if (lastGroups.length) renderGroups(lastGroups)
        else if (scannedItems.length) renderScannedPreview(scannedItems)
        classifyButton.disabled = scannedItems.length === 0
        extractButton.disabled = true
        if (isHistory) { enterScanMode(); if (classifiedItems.length) revealSaveButton() }
      } else {
        clearSavedState()
      }
    }

    // DB 权威源优先：进入页面先 GET scan-entries 渲染已落库条目（含已提取灰显），
    // 失败再回退 localStorage 离线缓存。已分类的（有 usefulness）按分组渲染，否则按扫描预览。
    const groupEntriesByClassification = (entries) => {
      const map = new Map()
      const order = []
      for (const entry of entries) {
        if (!entry.usefulness) continue
        const key = `${entry.usefulness}||${entry.subcategory || "未分类"}`
        if (!map.has(key)) {
          map.set(key, { usefulness: entry.usefulness, subcategory: entry.subcategory || "未分类", items: [] })
          order.push(key)
        }
        map.get(key).items.push(entry)
      }
      return order.map((key) => {
        const group = map.get(key)
        group.count = group.items.length
        return group
      })
    }

    const loadFromServer = async () => {
      if (collectionKind !== "history" && !resumablePlatforms.has(platform)) return false
      try {
        const params = new URLSearchParams({ platform, kind: collectionKind })
        const response = await fetch(`/api/sync/scan-entries?${params.toString()}`)
        if (!response.ok) return false
        const body = await response.json()
        const historySaved = body.history_saved === true
        const entries = body.items || []
        if (!entries.length) {
          if (isHistory) {
            // 历史 Tab 无条目：进扫描模式（保存态没意义，没东西可读）。回退 localStorage 由调用方决定。
            enterScanMode()
            return false
          }
          // 新增 Tab：DB 是唯一权威源，空就明确渲染空态，绝不回退 localStorage 旧缓存。
          scannedItems = []
          classifiedItems = []
          lastGroups = []
          if (results) { results.hidden = true; results.innerHTML = "" }
          if (filterToolbar) filterToolbar.hidden = true
          if (summary) {
            summary.hidden = false
            summary.textContent = "暂无新增收藏。点击「采集新增收藏」抓取历史之后新收的内容。"
          }
          setStatus("暂无新增收藏。已见过的条目会自动去重，不再重复展示。", "")
          classifyButton.disabled = true
          extractButton.disabled = true
          return true
        }
        scannedItems = entries
        const groups = groupEntriesByClassification(entries)
        if (groups.length) {
          classifiedItems = groups.flatMap((group) => group.items || [])
          renderGroups(groups)
          extractButton.disabled = classifiedItems.length === 0
          const usefulCount = entries.filter((e) => e.usefulness === "useful").length
          const extractedCount = entries.filter((e) => e.extracted === true).length
          if (summary) {
            summary.hidden = false
            summary.textContent = `已从本地数据库恢复 ${entries.length} 条（有用 ${usefulCount} 条，已入库 ${extractedCount} 条）。已入库的默认不勾选。`
          }
          setStatus("已从本地数据库恢复分类结果。", "ok")
          // 历史已保存 → 只读模式；否则有分组但未保存 → 扫描模式 + 露出「保存历史收藏」让用户补存。
          if (isHistory) {
            if (historySaved) enterReadOnlyMode()
            else { enterScanMode(); revealSaveButton() }
          }
        } else {
          renderScannedPreview(entries)
          if (summary) {
            summary.hidden = false
            summary.textContent = `已从本地数据库恢复 ${entries.length} 条扫描记录。下一步点击 AI 分类。`
          }
          setStatus("已从本地数据库恢复扫描结果。", "ok")
          if (isHistory) { if (historySaved) enterReadOnlyMode(); else enterScanMode() }
        }
        classifyButton.disabled = scannedItems.length === 0
        return true
      } catch (_error) {
        return false
      }
    }

    loadFromServer().then((loaded) => {
      if (!loaded) restoreState()
    })

    // 「保存历史收藏」：翻 history_saved flag（保存全部已分类条目，忽略勾选），转只读模式。
    saveHistoryButton?.addEventListener("click", async () => {
      setBusy(saveHistoryButton, true, "保存中...")
      try {
        const body = await apiPost("/api/sync/save-history", { platform })
        if (summary) {
          summary.hidden = false
          summary.textContent = `已保存 ${body.history_count != null ? body.history_count : scannedItems.length} 条历史收藏。下次进入历史收藏将直接展示，不再需要重新扫描分类。`
        }
        setStatus("历史收藏已保存。", "ok")
        enterReadOnlyMode()
      } catch (error) {
        setStatus(error.message || "保存失败", "bad")
      } finally {
        setBusy(saveHistoryButton, false)
      }
    })

    // 「重新扫描历史」：清 history_saved + first_scan_done，回到扫描模式重新走 history 全量。
    rescanHistoryButton?.addEventListener("click", async () => {
      setBusy(rescanHistoryButton, true, "重置中...")
      try {
        await apiPost("/api/sync/reset-history", { platform })
        setStatus("已重置历史收藏，可重新扫描。", "ok")
        enterScanMode()
      } catch (error) {
        setStatus(error.message || "重置失败", "bad")
      } finally {
        setBusy(rescanHistoryButton, false)
      }
    })

    scanButton?.addEventListener("click", async () => {
      setBusy(scanButton, true, isHistory ? "扫描中..." : "采集中...")
      setStatus("正在通过浏览器读取收藏标题，请不要关闭浏览器。")
      try {
        const currentHomepageUrl = homepageInput?.value?.trim() || root.dataset.homepageUrl || ""
        // 历史用下拉的采集数量；新增没有下拉、固定扫全量（limit:"all"），后端 filter_incremental 在全量上去重。
        const scanLimit = isHistory ? (limitSelect?.value || 10) : "all"
        const body = await apiPost("/api/sync/scan-titles", { platform, limit: scanLimit, homepage_url: currentHomepageUrl, collection_kind: collectionKind })
        scannedItems = body.items || []
        classifiedItems = []
        selectedCandidateIds = []
        if (body.all_duplicates) {
          setStatus(body.message || "收藏夹内容均已入库，请先在平台新增收藏后再扫描。", "bad")
          if (summary) { summary.hidden = false; summary.textContent = `扫描了 ${body.total_scanned} 条，全部已入库。`; }
        } else if (summary) {
          summary.hidden = false
          summary.textContent = `已扫描 ${body.total || scannedItems.length} 条收藏标题。下一步点击 AI 分类。`
        }
        renderScannedPreview(scannedItems)
        lastGroups = []
        saveState("scanned")
        classifyButton.disabled = scannedItems.length === 0
        extractButton.disabled = true
        setStatus("标题扫描完成。", "ok")
      } catch (error) {
        setStatus(error.message || "扫描失败", "bad")
      } finally {
        setBusy(scanButton, false)
      }
    })

    classifyButton?.addEventListener("click", async () => {
      setBusy(classifyButton, true, "分类中...")
      setStatus("正在调用已配置 AI 做有用/没用与二级分类。")
      try {
        const body = await apiPost("/api/classify/batch-titles", { items: scannedItems })
        const groups = body.groups || []
        classifiedItems = groups.flatMap((group) => group.items || [])
        if (summary) {
          summary.hidden = false
          summary.textContent = `有用 ${body.summary?.useful_count || 0} 条，没用 ${body.summary?.useless_count || 0} 条。默认只勾选“有用”，你可以调整。`
        }
        renderGroups(groups)
        extractButton.disabled = classifiedItems.length === 0
        setStatus("AI 分类完成，请确认要保留的分类或条目。", "ok")
        saveState("classified")
        // 历史 Tab：分类后露出「保存历史收藏」，让用户把全部已分类条目保存为只读。
        if (classifiedItems.length) revealSaveButton()
      } catch (error) {
        setStatus(error.message || "分类失败", "bad")
      } finally {
        setBusy(classifyButton, false)
      }
    })

    extractButton?.addEventListener("click", async () => {
      const checked = Array.from(root.querySelectorAll("[data-item-check]:checked"))
      const selected = checked.map((input) => classifiedItems[Number(input.dataset.itemIndex)]).filter(Boolean)
      const selectedSet = new Set(selected.map((item) => item.url))
      const skipped = classifiedItems.filter((item) => !selectedSet.has(item.url))
      if (!selected.length) {
        setStatus("请至少勾选一条要提取的收藏。", "bad")
        return
      }
      const isXiaohongshu = platform === "xiaohongshu"
      setBusy(extractButton, true, isXiaohongshu ? "点点提取中..." : "豆包提取中...")
      // Show progress modal
      const progressOverlay = document.createElement('div')
      progressOverlay.className = 'distill-overlay'
      progressOverlay.innerHTML = `
        <div style="background:#fff;border-radius:16px;padding:2rem;max-width:420px;width:90%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,0.12);">
          <h2 style="margin:0 0 8px;font-size:1.1rem;">🤖 ${isXiaohongshu ? '点点' : '豆包'}正在提取</h2>
          <div style="width:100%;height:8px;background:#eee;border-radius:4px;margin:16px 0;overflow:hidden;">
            <div id="extract-progress-bar" style="height:100%;background:linear-gradient(90deg,#215f52,#3a9e85);border-radius:4px;width:5%;transition:width 0.5s;"></div>
          </div>
          <p id="extract-progress-text" style="font-size:0.9rem;color:#555;margin:0 0 8px;">准备中...</p>
          <div id="extract-progress-log" style="max-height:150px;overflow-y:auto;text-align:left;background:#fafafa;border-radius:8px;padding:8px;font-size:0.8rem;color:#555;"></div>
        </div>`
      document.body.appendChild(progressOverlay)
      const pBar = document.getElementById('extract-progress-bar')
      const pText = document.getElementById('extract-progress-text')
      const pLog = document.getElementById('extract-progress-log')
      function updateExtractProgress(current, total, msg) {
        const pct = Math.max(5, Math.round((current / total) * 100))
        pBar.style.width = pct + '%'
        pText.textContent = msg
      }
      function addExtractLog(msg) {
        pLog.innerHTML += `<div style="padding:2px 0;border-bottom:1px solid #f0f0f0;">${msg}</div>`
        pLog.scrollTop = pLog.scrollHeight
      }
      setStatus(isXiaohongshu
        ? `已选择 ${selected.length} 条。正在发送到小红书点点提取。`
        : `已选择 ${selected.length} 条。正在发送到豆包提取。`)
      try {
        updateExtractProgress(0, selected.length, '正在准备候选...')
        addExtractLog('📋 准备 ' + selected.length + ' 条待提取内容')
        const prepared = await apiPost("/api/sync/prepare-selected", { platform, selected_items: selected, skipped_items: skipped })
        const preparedIds = prepared.candidate_ids || []
        if (preparedIds.length) selectedCandidateIds = preparedIds
        saveState("prepared")
        if (!selectedCandidateIds.length) {
          progressOverlay.remove()
          setStatus("没有可提取的候选。可能这些内容已经准备过或已入库，请重新扫描/分类后再试。", "bad")
          return
        }
        currentJobId = null
        await runExtraction(isXiaohongshu)
      } catch (error) {
        handleExtractError(error, isXiaohongshu)
      } finally {
        setBusy(extractButton, false)
      }
    })

    // 续跑：用户在豆包/点点页面手动完成人机验证后点此按钮。
    // 带上原 candidate_ids + currentJobId 重 POST 同一端点，后端靠 *_extracted 标记
    // 跳过已完成、从断点续跑。循环直到 status === "completed"。
    resumeButton?.addEventListener("click", async () => {
      if (!selectedCandidateIds.length) {
        setStatus("没有可续跑的候选。", "bad")
        return
      }
      const isXiaohongshu = platform === "xiaohongshu"
      resumeButton.hidden = true
      setBusy(extractButton, true, isXiaohongshu ? "点点续跑中..." : "豆包续跑中...")
      setStatus("正在从断点继续提取剩余条目...")
      try {
        await runExtraction(isXiaohongshu)
      } catch (error) {
        handleExtractError(error, isXiaohongshu)
      } finally {
        setBusy(extractButton, false)
      }
    })

    // 单次 POST extract-selected，根据返回的 status 处理 paused / completed。
    // paused 是 200（不是错误），据此显示续跑按钮 + reason 提示。
    async function runExtraction(isXiaohongshu) {
      const extractEndpoint = isXiaohongshu ? "/api/xiaohongshu/diandian/extract-selected" : "/api/doubao/extract-selected"
      const payload = { candidate_ids: selectedCandidateIds, per_item_timeout_seconds: 240 }
      if (currentJobId) payload.job_id = currentJobId
      const extracted = await apiPost(extractEndpoint, payload)
      currentJobId = extracted.job_id || currentJobId
      if (extracted.status === "paused") {
        const remaining = extracted.pending_remaining != null ? extracted.pending_remaining : "若干"
        const baseMsg = extracted.message || "检测到人机验证，已暂停。请在浏览器页面完成验证后继续。"
        setStatus(`${baseMsg}（本轮已入库 ${extracted.success_count || 0} 条，剩余 ${remaining} 条待续跑）`, "bad")
        if (resumeButton) resumeButton.hidden = false
        saveState("paused")
        return
      }
      // completed
      if (resumeButton) resumeButton.hidden = true
      setStatus(`完成：成功入库 ${extracted.success_count || 0} 条，失败 ${extracted.failed_count || 0} 条。`, "ok")
      if (summary) summary.textContent = `RawSource 已写入，可前往“原始资料”查看。候选 ID：${selectedCandidateIds.join(", ")}`
      saveState("completed")
    }

    function handleExtractError(error, isXiaohongshu) {
      if (error.code === "xiaohongshu_diandian_not_ready") {
        setStatus("小红书点点页面未就绪。请确认浏览器仍登录小红书，并打开 https://www.xiaohongshu.com/ai_chat 后重试。", "bad")
      } else if (error.code === "doubao_login_required") {
        setStatus("需要先登录豆包。系统已打开豆包页面，请在浏览器完成登录；如果豆包没有主动弹窗，请在豆包页面发送任意一句话触发登录弹窗，登录完成后回到这里重试。豆包登录入口：https://www.doubao.com", "bad")
      } else {
        setStatus(error.message || (isXiaohongshu ? "小红书点点提取失败" : "豆包提取失败"), "bad")
      }
    }
  }
  document.querySelectorAll("[data-source-filter]").forEach(initSourceFilter)

  // 同步收藏页：顶部平台标签栏，点标签按需 AJAX 注入该平台提取面板，URL 同步 ?platform=。
  // 独立页 /ui/source-setup/{platform} 仍在（片段来源 + 旧书签兼容）。
  document.querySelectorAll("[data-platform-tabs]").forEach((tabbar) => {
    const section = tabbar.closest("[data-platform-section]") || tabbar.parentElement
    const host = section?.querySelector("[data-platform-panel-host]")
    if (!host) return
    const tabs = Array.from(tabbar.querySelectorAll("[data-platform-tab]"))
    const validPlatforms = new Set(tabs.map((tab) => tab.dataset.platformTab))

    const highlight = (platform) => {
      tabs.forEach((tab) => {
        const active = tab.dataset.platformTab === platform
        tab.classList.toggle("is-active", active)
        tab.setAttribute("aria-selected", active ? "true" : "false")
      })
    }

    let loadToken = 0
    const loadPanel = async (platform) => {
      if (!validPlatforms.has(platform)) return
      const token = ++loadToken
      highlight(platform)
      host.innerHTML = '<p class="platform-panel-loading">正在加载提取面板…</p>'
      try {
        const response = await fetch(`/ui/source-setup/${encodeURIComponent(platform)}/panel`, {
          headers: { "X-Requested-With": "fetch" },
        })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const html = await response.text()
        if (token !== loadToken) return // 期间又点了别的标签，丢弃过期响应
        host.innerHTML = html
        host.querySelectorAll("[data-source-shell]").forEach(initSourceShell)
        host.querySelectorAll("[data-source-filter]").forEach(initSourceFilter)
      } catch (_error) {
        if (token !== loadToken) return
        host.innerHTML =
          `<div class="notice bad">提取面板加载失败。` +
          `<a href="/ui/source-setup/${encodeURIComponent(platform)}">打开独立页</a>重试。</div>`
      }
    }

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const platform = tab.dataset.platformTab
        loadPanel(platform)
        try {
          window.history.pushState({ platform }, "", `/ui/sync?platform=${encodeURIComponent(platform)}`)
        } catch (_ignored) {}
      })
    })

    window.addEventListener("popstate", () => {
      const params = new URLSearchParams(window.location.search)
      const platform = params.get("platform")
      if (platform && validPlatforms.has(platform)) loadPanel(platform)
    })

    // 首屏：URL ?platform= 优先，否则 host 的默认平台。
    const initialParams = new URLSearchParams(window.location.search)
    const requested = initialParams.get("platform")
    const initial = (requested && validPlatforms.has(requested))
      ? requested
      : host.dataset.defaultPlatform
    if (initial && validPlatforms.has(initial)) loadPanel(initial)
  })

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.hasAttribute("data-no-busy")) return
      if (form.dataset.confirmMessage && !window.confirm(form.dataset.confirmMessage)) {
        event.preventDefault()
        return
      }
      const button = form.querySelector("button[type='submit']")
      if (!button || button.dataset.busyApplied === "true") return
      button.dataset.busyApplied = "true"
      button.dataset.originalText = button.textContent || ""
      button.textContent = button.dataset.busyText || "处理中..."
      button.disabled = true
      button.classList.add("is-submitting")
    })
  })
})();

// ─── Push Notification Polling ──────────────────────────────────────────────
(function() {
  var pollOwnerKey = 'StarMind_push_poll_owner';
  var pollOwnerId = String(Date.now()) + '-' + Math.random().toString(16).slice(2);

  function ownsPushPolling() {
    try {
      var now = Date.now();
      var current = JSON.parse(localStorage.getItem(pollOwnerKey) || '{}') || {};
      if (current.id && current.id !== pollOwnerId && now - Number(current.at || 0) < 15000) return false;
      localStorage.setItem(pollOwnerKey, JSON.stringify({id: pollOwnerId, at: now}));
      return true;
    } catch(e) {
      return true;
    }
  }

  function showFeedbackBanner(item) {
    if (document.querySelector('[data-push-feedback-banner]')) return;
    var banner = document.createElement('div');
    banner.setAttribute('data-push-feedback-banner', 'true');
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#fff;border-bottom:2px solid #215f52;padding:16px 24px;box-shadow:0 4px 16px rgba(0,0,0,0.1);display:flex;align-items:center;gap:16px;';
    banner.innerHTML = '<div style="flex:1;"><strong>这次推送对你有帮助吗？</strong><div style="font-size:.85rem;color:#666;margin-top:4px;">【' + item.category + '】' + item.title + '</div></div><button class="fb-like" style="border:none;background:#215f52;color:#fff;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:.9rem;">有帮助</button><button class="fb-unlike" style="border:none;background:#eee;color:#333;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:.9rem;">不相关</button>';
    document.body.appendChild(banner);

    function sendFeedback(fb) {
      fetch('/api/push/preference-feedback', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({push_history_id:item.push_id,feedback:fb})});
      banner.remove();
    }
    banner.querySelector('.fb-like').onclick = function(){ sendFeedback('like'); };
    banner.querySelector('.fb-unlike').onclick = function(){ sendFeedback('unlike'); };
    setTimeout(function(){ if(banner.parentElement) banner.remove(); }, 60000);
  }

  // Notification polling (only if permission granted)
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;

  async function pollPushItems() {
    if (!ownsPushPolling()) return;
    try {
      const res = await fetch('/api/push/items');
      const items = await res.json();
      if (!items.length) return;
      items.forEach(item => {
        new Notification('StarMind 知识推送', {
          body: '【' + item.category + '】' + item.title + '\n' + item.summary,
          tag: 'push-' + item.push_id
        });
        if (item.show_feedback) showFeedbackBanner(item);
      });
    } catch(e) {}
  }

  pollPushItems();
  setInterval(pollPushItems, 10000);
})();

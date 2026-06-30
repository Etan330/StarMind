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

  function activateCollectionTab(shell, kind) {
    if (!shell) return
    const tabs = Array.from(shell.querySelectorAll("[data-collection-tab]"))
    const panels = Array.from(shell.querySelectorAll("[data-source-filter]"))
    const hints = Array.from(shell.querySelectorAll("[data-collection-hint]"))
    tabs.forEach((tab) => {
      const active = tab.dataset.collectionTab === kind
      tab.classList.toggle("is-active", active)
      tab.setAttribute("aria-selected", active ? "true" : "false")
    })
    panels.forEach((panel) => {
      const match = panel.dataset.collectionKind === kind
      panel.hidden = !match
      panel.classList.toggle("is-hidden", !match)
    })
    hints.forEach((hint) => {
      hint.hidden = hint.dataset.collectionHint !== kind
    })
  }

  function initSourceShell(shell) {
    if (!shell || shell.dataset.bound === "1") return
    shell.dataset.bound = "1"
    shell.querySelectorAll("[data-collection-tab]").forEach((tab) => {
      tab.addEventListener("click", () => activateCollectionTab(shell, tab.dataset.collectionTab))
    })
  }

  async function refreshSiblingHistoryPanel(root) {
    const shell = root?.closest("[data-source-shell]")
    const historyPanel = shell?.querySelector('[data-source-filter][data-collection-kind="history"]')
    if (!historyPanel || historyPanel === root) return
    historyPanel.dispatchEvent(new CustomEvent("starmind:refresh-history"))
  }

  document.querySelectorAll("[data-source-shell]").forEach(initSourceShell)


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

  const extractStatusLabel = {
    pending: "待提取",
    extracting: "提取中",
    ingested: "已入库",
    failed: "失败",
    needs_login: "需处理",
  }

  const extractStatusTone = {
    pending: "",
    extracting: "primary",
    ingested: "success",
    failed: "danger",
    needs_login: "danger",
  }

  function showExtractCompletionNotice(payload = {}) {
    const success = payload.success_count || 0
    const failed = payload.failed_count || 0
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      new Notification("StarMind 提取完成", {
        body: `成功入库 ${success} 条，失败 ${failed} 条。`,
        tag: `extract-${payload.job_id || Date.now()}`,
      })
    }
  }

  function createExtractOverlay(title) {
    const overlay = document.createElement("div")
    overlay.className = "extract-overlay"
    overlay.dataset.dismissed = "0"
    overlay.innerHTML = `
      <section class="extract-modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
        <header class="extract-modal-head">
          <div>
            <h2>${escapeHtml(title)}</h2>
            <p data-extract-message>准备中...</p>
          </div>
          <button class="extract-close" type="button" data-extract-dismiss aria-label="关闭进度窗口">×</button>
        </header>
        <div class="extract-progress-track"><span data-extract-progress style="width:5%"></span></div>
        <div class="extract-item-list" data-extract-items></div>
      </section>`
    document.body.appendChild(overlay)
    overlay.querySelector("[data-extract-dismiss]")?.addEventListener("click", (event) => {
      event.preventDefault()
      event.stopPropagation()
      overlay.dataset.dismissed = "1"
      overlay.remove()
    })
    return overlay
  }

  function renderExtractOverlay(overlay, payload = {}) {
    if (!overlay || overlay.dataset.dismissed === "1") return
    const items = payload.items || []
    const total = payload.total || items.length || 1
    const done = items.filter((item) => ["ingested", "failed", "needs_login"].includes(item.status)).length
    const message = payload.message || (payload.status === "completed" ? "提取完成。" : `正在提取 ${done}/${total}`)
    const progress = Math.max(5, Math.round((done / total) * 100))
    const messageEl = overlay.querySelector("[data-extract-message]")
    const progressEl = overlay.querySelector("[data-extract-progress]")
    const listEl = overlay.querySelector("[data-extract-items]")
    if (messageEl) messageEl.textContent = message
    if (progressEl) progressEl.style.width = `${progress}%`
    if (listEl) {
      listEl.innerHTML = items.map((item) => {
        const tone = extractStatusTone[item.status] || ""
        const label = extractStatusLabel[item.status] || item.status || "待提取"
        const preview = item.preview?.content ? `
          <details class="extract-preview">
            <summary>展开正文</summary>
            <pre>${escapeHtml(item.preview.content)}</pre>
          </details>` : ""
        return `
          <article class="extract-item ${escapeHtml(item.status || "pending")}">
            <div>
              <strong>${escapeHtml(item.title || `候选 ${item.candidate_id}`)}</strong>
              ${item.error ? `<small>${escapeHtml(item.error)}</small>` : ""}
            </div>
            <span class="status-chip ${tone}">${escapeHtml(label)}</span>
            ${preview}
          </article>`
      }).join("") || '<div class="empty-state">等待任务开始。</div>'
    }
  }

  function showExtractPauseDialog({ message, onResume }) {
    const overlay = document.createElement("div")
    overlay.className = "extract-overlay extract-overlay--danger"
    overlay.innerHTML = `
      <section class="extract-modal extract-modal--danger" role="dialog" aria-modal="true">
        <header class="extract-modal-head">
          <h2>需要手动处理</h2>
          <p>${escapeHtml(message || "检测到登录或人机验证，请在浏览器页面完成处理后继续。")}</p>
        </header>
        <div class="extract-modal-actions">
          <button class="btn secondary" type="button" data-extract-close>稍后处理</button>
          <button class="btn primary" type="button" data-extract-resume>我已完成，继续</button>
        </div>
      </section>`
    document.body.appendChild(overlay)
    overlay.querySelector("[data-extract-close]")?.addEventListener("click", () => overlay.remove())
    overlay.querySelector("[data-extract-resume]")?.addEventListener("click", async () => {
      overlay.remove()
      await onResume?.()
    })
    return overlay
  }

  async function pollExtractJob(jobId, { overlay, onStatus } = {}) {
    if (!jobId) return null
    let lastPayload = null
    for (let attempt = 0; attempt < 720; attempt += 1) {
      const response = await fetch(`/api/extract/job-status?job_id=${encodeURIComponent(jobId)}`)
      if (response.status === 404) throw new Error("任务已结束或丢失，请重新发起；已提取的不会重复。")
      if (!response.ok) throw new Error(`查询任务失败：${response.status}`)
      lastPayload = await response.json()
      renderExtractOverlay(overlay, lastPayload)
      onStatus?.(lastPayload)
      if (["completed", "paused", "error"].includes(lastPayload.status)) {
        if (lastPayload.status === "completed" && overlay?.dataset.dismissed === "1") showExtractCompletionNotice(lastPayload)
        return lastPayload
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1500))
    }
    return lastPayload
  }

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
    const status = root.querySelector("[data-filter-status]")
    const summary = root.querySelector("[data-filter-summary]")
    const results = root.querySelector("[data-filter-results]")
    const filterToolbar = root.querySelector("[data-filter-toolbar]")
    const controls = root.querySelector("[data-filter-controls]")
    const usefulnessFilter = root.querySelector("[data-filter-usefulness]")
    const categoryFilter = root.querySelector("[data-filter-category]")
    const timeFilter = root.querySelector("[data-filter-time]")
    const ingestedFilter = root.querySelector("[data-filter-ingested]")
    const saveHistoryButton = root.querySelector("[data-filter-save-history]")
    const rescanHistoryButton = root.querySelector("[data-filter-rescan-history]")
    const clearHistoryButton = root.querySelector("[data-filter-clear-history]")
    const isHistory = collectionKind === "history"
    let scannedItems = []
    let classifiedItems = []
    let selectedCandidateIds = []
    let currentJobId = null
    let lastGroups = []
    const resumablePlatforms = new Set(["douyin", "xiaohongshu", "bilibili"])
    const stateKey = `starmind.batchTitleFilter.${platform}.${collectionKind}`
    try { window.localStorage?.removeItem(stateKey) } catch (_ignored) {}

    const show = (el, visible) => { if (el) el.hidden = !visible }
    const setControlsLayout = (mode) => {
      if (!controls) return
      controls.classList.toggle("source-filter-controls--saved", mode === "saved")
      controls.classList.toggle("source-filter-controls--scan", mode !== "saved")
    }

    // 历史 Tab 三态布局（gate 在 isHistory）：
    // - 扫描模式：采集数量/扫描/分类可用，藏 保存/重新扫描/续跑。
    // - 只读模式（已保存历史）：藏 采集数量/扫描/分类/保存，留 重新扫描/提取/续跑，并切到三列紧凑排版。
    const enterScanMode = () => {
      setControlsLayout("scan")
      show(extractButton, true)
    }
    const revealSaveButton = () => {}
    const enterReadOnlyMode = () => {
      setControlsLayout("saved")
      show(extractButton, true)
    }

    const setStatus = (message, tone = "") => {
      if (!status) return
      status.textContent = message
      status.dataset.tone = tone
    }

    const readSavedState = () => null
    const saveState = () => {}
    const clearSavedState = () => {
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
      if (filterToolbar) filterToolbar.hidden = !(isHistory || lastGroups.length > 0)
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
                const candidateAttr = item.candidate_id ? ` data-candidate-id="${escapeHtml(item.candidate_id)}"` : ""
                return `
                <label class="filter-item${extracted ? " extracted" : ""}">
                  <input type="checkbox" data-item-check data-item-index="${idx}"${candidateAttr} ${checked} ${disabled}>
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

    const restoreState = () => {}

    // 历史 Tab 进入页面从 DB 恢复；新增 Tab 是本次采集临时视图，刷新后保持空态。
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
      if (!isHistory) {
        scannedItems = []
        classifiedItems = []
        lastGroups = []
        if (results) { results.hidden = true; results.innerHTML = "" }
        if (filterToolbar) filterToolbar.hidden = true
        if (summary) {
          summary.hidden = false
          summary.textContent = "暂无新增收藏。点击「采集收藏」抓取本次新增内容。"
        }
        setStatus("新增收藏会在采集后展示；刷新页面后这里会清空，内容仍在历史中。", "")
        if (classifyButton) classifyButton.disabled = true
        if (extractButton) extractButton.disabled = true
        return true
      }
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
            summary.textContent = "暂无新增收藏。点击「采集收藏」抓取历史之后新收的内容。"
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

    root.addEventListener("starmind:refresh-history", async () => {
      if (!isHistory) return
      await loadFromServer()
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
        clearSavedState()
        setStatus("历史收藏已保存。", "ok")
        enterReadOnlyMode()
      } catch (error) {
        setStatus(error.message || "保存失败", "bad")
      } finally {
        setBusy(saveHistoryButton, false)
      }
    })

    const clearHistoryList = async (button) => {
      if (!window.confirm("确定清空当前收藏列表吗？已入库的原始资料和 Wiki 不会被删除。")) return
      setBusy(button, true, "清空中...")
      try {
        const body = await apiPost("/api/sync/clear-history-list", { platform })
        scannedItems = []
        classifiedItems = []
        selectedCandidateIds = []
        lastGroups = []
        clearSavedState()
        if (results) { results.hidden = true; results.innerHTML = "" }
        if (filterToolbar) filterToolbar.hidden = true
        if (summary) {
          summary.hidden = false
          summary.textContent = `已清空 ${body.scan_entry_count || 0} 条当前收藏列表记录。可到新增收藏重新扫描。`
        }
        if (classifyButton) classifyButton.disabled = true
        if (extractButton) extractButton.disabled = true
        setStatus("当前收藏列表已清空。可到新增收藏选择数量后重新采集。", "ok")
        enterScanMode()
      } catch (error) {
        setStatus(error.message || "清空失败", "bad")
      } finally {
        setBusy(button, false)
      }
    }

    // 「重新扫描历史」：清 history_saved + first_scan_done，回到扫描模式重新走 history 全量。
    rescanHistoryButton?.addEventListener("click", async () => {
      await clearHistoryList(rescanHistoryButton)
    })

    clearHistoryButton?.addEventListener("click", async () => {
      await clearHistoryList(clearHistoryButton)
    })

    scanButton?.addEventListener("click", async () => {
      setBusy(scanButton, true, isHistory ? "扫描中..." : "采集中...")
      setStatus("正在通过浏览器读取收藏标题，请不要关闭浏览器。")
      try {
        const currentHomepageUrl = homepageInput?.value?.trim() || root.dataset.homepageUrl || ""
        const selectedLimit = limitSelect?.value || "new"
        const scanLimit = "all"
        const body = await apiPost("/api/sync/scan-titles", { platform, limit: scanLimit, scan_mode: selectedLimit, homepage_url: currentHomepageUrl, collection_kind: collectionKind })
        scannedItems = body.items || []
        classifiedItems = []
        selectedCandidateIds = []
        if (body.all_duplicates) {
          setStatus(body.message || "没有新增收藏。历史收藏中已有这些内容。", "ok")
          if (summary) { summary.hidden = false; summary.textContent = body.message || "没有新增收藏。"; }
        } else if (summary) {
          summary.hidden = false
          const skipped = body.skipped_existing_count || 0
          const savedHint = body.saved_to_history ? "已保存到历史收藏。" : ""
          summary.textContent = `发现 ${body.new_count ?? body.total ?? scannedItems.length} 条新增收藏，已跳过 ${skipped} 条历史中已有内容。${savedHint}下一步点击 AI 分类。`
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
          const historyHint = body.saved_to_history !== false ? "已保存到历史收藏，可在历史收藏继续筛选或补提取。" : ""
          summary.textContent = `有用 ${body.summary?.useful_count || 0} 条，没用 ${body.summary?.useless_count || 0} 条。${historyHint}默认只勾选“有用”，你可以调整。`
        }
        renderGroups(groups)
        extractButton.disabled = classifiedItems.length === 0
        await refreshSiblingHistoryPanel(root)
        setStatus("AI 分类完成，已同步更新到历史收藏。请勾选要提取的分类或条目。", "ok")
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
      const selected = checked.map((input) => {
        const item = classifiedItems[Number(input.dataset.itemIndex)]
        if (!item) return null
        return input.dataset.candidateId ? { ...item, candidate_id: input.dataset.candidateId } : item
      }).filter(Boolean)
      const selectedSet = new Set(selected.map((item) => item.url))
      const skipped = classifiedItems.filter((item) => !selectedSet.has(item.url))
      if (!selected.length) {
        setStatus("请至少勾选一条要提取的收藏。", "bad")
        return
      }
      const isXiaohongshu = platform === "xiaohongshu"
      setBusy(extractButton, true, isXiaohongshu ? "点点提取中..." : "豆包提取中...")
      setStatus(isXiaohongshu ? `已选择 ${selected.length} 条。正在发送到小红书点点提取。` : `已选择 ${selected.length} 条。正在发送到豆包提取。`)
      try {
        const prepared = await apiPost("/api/sync/prepare-selected", { platform, selected_items: selected, skipped_items: skipped })
        const preparedIds = prepared.candidate_ids || []
        if (preparedIds.length) selectedCandidateIds = preparedIds
        if (!selectedCandidateIds.length) {
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

    async function runExtraction(isXiaohongshu, existingOverlay = null) {
      const extractEndpoint = isXiaohongshu ? "/api/xiaohongshu/diandian/extract-selected" : "/api/doubao/extract-selected"
      const payload = { candidate_ids: selectedCandidateIds, per_item_timeout_seconds: 240, async_job: true }
      if (currentJobId) payload.job_id = currentJobId
      const overlay = existingOverlay || createExtractOverlay(`${isXiaohongshu ? "点点" : "豆包"}正在提取`)
      renderExtractOverlay(overlay, { total: selectedCandidateIds.length, items: selectedCandidateIds.map((candidateId) => ({ candidate_id: candidateId, status: "pending" })) })
      const extracted = await apiPost(extractEndpoint, payload)
      currentJobId = extracted.job_id || currentJobId
      const finalStatus = await pollExtractJob(currentJobId, { overlay })
      const payloadStatus = finalStatus || extracted
      if (payloadStatus.status === "paused") {
        const remaining = extracted.pending_remaining != null ? extracted.pending_remaining : "若干"
        const baseMsg = payloadStatus.message || extracted.message || "检测到人机验证，已暂停。请在浏览器页面完成验证后继续。"
        setStatus(`${baseMsg}（本轮已入库 ${payloadStatus.success_count || 0} 条，剩余 ${remaining} 条待续跑）`, "bad")
        showExtractPauseDialog({
          message: baseMsg,
          onResume: async () => {
            await runExtraction(isXiaohongshu, overlay)
          },
        })
        return
      }
      if (payloadStatus.status === "error") {
        throw new Error(payloadStatus.error || "提取任务失败")
      }
      setStatus(`完成：成功入库 ${payloadStatus.success_count || 0} 条，失败 ${payloadStatus.failed_count || 0} 条。`, "ok")
      if (summary) {
        summary.hidden = false
        summary.textContent = `RawSource 已写入，可前往“原始资料”查看。候选 ID：${selectedCandidateIds.join(", ")}`
      }
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

  function initLinkExtractPanel(panel) {
    if (!panel || panel.dataset.bound === "1") return
    panel.dataset.bound = "1"
    const form = panel.querySelector("[data-link-extract-form]")
    const submitButton = panel.querySelector("[data-link-extract-submit]")
    const status = panel.querySelector("[data-link-extract-status]")
    const summary = panel.querySelector("[data-link-extract-summary]")
    const preview = panel.querySelector("[data-link-extract-preview]")
    const previewTitle = panel.querySelector("[data-link-extract-preview-title]")
    const previewSource = panel.querySelector("[data-link-extract-preview-source]")
    const previewContent = panel.querySelector("[data-link-extract-preview-content]")
    let selectedCandidateIds = []
    let extractEndpoint = ""
    let currentJobId = null
    const setStatusText = (message, tone) => {
      if (!status) return
      status.textContent = message
      status.dataset.tone = tone || ""
    }
    const showPreview = (payload) => {
      const data = payload?.preview || payload
      if (!preview || !data || !data.content) return
      preview.hidden = false
      if (previewTitle) previewTitle.textContent = data.title || payload?.title || "提取结果"
      if (previewContent) previewContent.innerHTML = escapeHtml(data.content)
      if (previewSource && data.raw_source_id) {
        previewSource.hidden = false
        previewSource.href = `/ui/sources?source_id=${encodeURIComponent(data.raw_source_id)}`
      }
    }
    const showFirstPreview = (items = []) => {
      const item = items.find((current) => current && current.success && current.preview?.content)
      if (item) showPreview(item)
    }
    const runExtraction = async (existingOverlay = null) => {
      if (!extractEndpoint || !selectedCandidateIds.length) return
      const payload = { candidate_ids: selectedCandidateIds, per_item_timeout_seconds: 240, async_job: true }
      if (currentJobId) payload.job_id = currentJobId
      const overlay = existingOverlay || createExtractOverlay("链接内容正在提取")
      renderExtractOverlay(overlay, { total: selectedCandidateIds.length, items: selectedCandidateIds.map((candidateId) => ({ candidate_id: candidateId, status: "pending" })) })
      const extracted = await apiPost(extractEndpoint, payload)
      currentJobId = extracted.job_id || currentJobId
      const finalStatus = await pollExtractJob(currentJobId, { overlay })
      const payloadStatus = finalStatus || extracted
      if (payloadStatus.status === "paused") {
        const remaining = extracted.pending_remaining != null ? extracted.pending_remaining : "若干"
        const message = payloadStatus.message || extracted.message || "检测到登录或人机验证，已暂停。"
        setStatusText(`${message} 本轮已入库 ${payloadStatus.success_count || 0} 条，剩余 ${remaining} 条。`, "bad")
        showExtractPauseDialog({
          message,
          onResume: async () => {
            await runExtraction(overlay)
          },
        })
        return
      }
      if (payloadStatus.status === "error") throw new Error(payloadStatus.error || "提取任务失败")
      setStatusText(`完成：成功入库 ${payloadStatus.success_count || 0} 条，失败 ${payloadStatus.failed_count || 0} 条。`, "ok")
      if (summary) {
        summary.hidden = false
        summary.textContent = `RawSource 已写入，可前往“原始资料”查看。候选 ID：${selectedCandidateIds.join(", ")}`
      }
      showFirstPreview((payloadStatus.items || extracted.items) || [])
    }
    form?.addEventListener("submit", async (event) => {
      event.preventDefault()
      setBusy(submitButton, true, "准备中...")
      setStatusText("正在识别平台并准备提取。")
      try {
        const data = Object.fromEntries(new FormData(form).entries())
        const prepared = await apiPost("/api/intake/link-extract", data)
        if (prepared.status === "ingested") {
          selectedCandidateIds = prepared.candidate_id ? [prepared.candidate_id] : []
          extractEndpoint = ""
          currentJobId = null
          setStatusText("已提取微信公众号正文并写入原始资料库。", "ok")
          if (summary) {
            summary.hidden = false
            summary.textContent = `RawSource 已写入，可前往“原始资料”查看。候选 ID：${prepared.candidate_id || "-"}`
          }
          showPreview(prepared)
          return
        }
        selectedCandidateIds = prepared.candidate_ids || []
        extractEndpoint = prepared.extract_endpoint || ""
        currentJobId = null
        setStatusText(prepared.platform === "xiaohongshu" ? "已识别为小红书，正在发送到点点。" : "已识别为抖音，正在发送到豆包。")
        await runExtraction()
      } catch (error) {
        const detail = error?.detail || {}
        if (detail.code === "unsupported_link_extract_platform") {
          setStatusText(detail.message || "当前平台暂不支持自动提取。", "bad")
        } else if (error.code === "xiaohongshu_diandian_not_ready") {
          setStatusText("小红书点点页面未就绪。请打开 https://www.xiaohongshu.com/ai_chat 后重试。", "bad")
        } else if (error.code === "doubao_login_required") {
          setStatusText("需要先登录豆包。系统已打开豆包页面，请在浏览器完成登录后回到这里重试。", "bad")
        } else {
          setStatusText(error.message || "链接提取失败", "bad")
        }
      } finally {
        setBusy(submitButton, false)
      }
    })
  }
  document.querySelectorAll("[data-link-extract-panel]").forEach(initLinkExtractPanel)

  // 博主蒸馏面板初始化
  function initCreatorDistillPanel(panel) {
    if (!panel || panel.dataset.bound === "1") return
    panel.dataset.bound = "1"

    const tabbar = panel.querySelector("[data-creator-platform-tabs]")
    const tabs = Array.from(tabbar?.querySelectorAll("[data-creator-platform-tab]") || [])
    const creatorInput = panel.querySelector("[data-creator-input]")
    const scanButton = panel.querySelector("[data-creator-scan]")
    const extractButton = panel.querySelector("[data-creator-extract]")
    const status = panel.querySelector("[data-creator-status]")
    const results = panel.querySelector("[data-creator-results]")

    const creatorProgressKey = "creatorDistillProgress:v1"
    let currentPlatform = "douyin"
    let scannedItems = []
    let selectedItemIds = []
    let currentCreator = null
    let selectedCandidateIds = []
    let currentJobId = null

    const setStatusText = (message, tone) => {
      if (!status) return
      status.textContent = message
      status.dataset.tone = tone || ""
    }

    const numberText = (value) => {
      const numeric = Number(value || 0)
      if (!numeric) return ""
      if (numeric >= 10000) return `${(numeric / 10000).toFixed(numeric >= 100000 ? 0 : 1)}万`
      return String(numeric)
    }

    const cleanCreatorWorkTitle = (value) => String(value || "")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/^(?:(?:点赞|赞|收藏|喜欢)\s*[0-9]+(?:\.[0-9]+)?\s*(?:万|亿|w|k)?|[0-9]+(?:\.[0-9]+)?\s*(?:万|亿|w|k))\s+/i, "")
      .trim()

    const normalizeCreatorWorkItem = (item) => ({
      ...item,
      title: cleanCreatorWorkTitle(item.title) || item.title || "未命名作品",
    })

    const saveCreatorState = () => {
      try {
        window.localStorage?.setItem(creatorProgressKey, JSON.stringify({
          version: 1,
          platform: currentPlatform,
          selectedCandidateIds,
          currentJobId,
          updatedAt: new Date().toISOString(),
        }))
      } catch (_error) {}
    }

    const renderCreatorProfile = () => {
      if (!results || !currentCreator) return ""
      const name = currentCreator.creator_name || "未命名博主"
      const fans = numberText(currentCreator.follower_count)
      const liked = numberText(currentCreator.liked_count)
      return `
        <section class="creator-profile-card" data-creator-profile>
          <strong>${escapeHtml(name)}</strong>
          <span>${fans ? `粉丝 ${escapeHtml(fans)}` : "粉丝数未抓到"}</span>
          <span>${liked ? `获赞 ${escapeHtml(liked)}` : "获赞数未抓到"}</span>
        </section>
      `
    }

    // 平台切换
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const platform = tab.dataset.creatorPlatformTab
        currentPlatform = platform
        tabs.forEach((t) => {
          const active = t.dataset.creatorPlatformTab === platform
          t.classList.toggle("is-active", active)
          t.setAttribute("aria-selected", active ? "true" : "false")
        })
        // 清空扫描结果
        scannedItems = []
        selectedItemIds = []
        currentCreator = null
        selectedCandidateIds = []
        currentJobId = null
        saveCreatorState()
        if (results) {
          results.innerHTML = ""
          results.hidden = true
        }
        if (extractButton) extractButton.disabled = true
        setStatusText(`已切换到${platform === "douyin" ? "抖音" : "小红书"}平台，请输入博主链接。`)
      })
    })

    // 扫描作品
    scanButton?.addEventListener("click", async () => {
      const creatorUrl = creatorInput?.value?.trim()
      if (!creatorUrl) {
        setStatusText("请先输入博主主页链接", "bad")
        return
      }

      setBusy(scanButton, true, "扫描中...")
      setStatusText("正在获取博主作品列表...")
      if (results) results.hidden = true

      try {
        // TODO: 替换为实际的 API 端点
        const response = await apiPost("/api/creator/scan", {
          platform: currentPlatform,
          creator_url: creatorUrl,
        })
        scannedItems = (response.items || []).map(normalizeCreatorWorkItem)
        currentCreator = response.creator || null
        selectedItemIds = []
        selectedCandidateIds = []
        currentJobId = null

        if (scannedItems.length === 0) {
          setStatusText("未找到作品，请检查链接是否正确。", "bad")
        } else {
          setStatusText(`找到 ${scannedItems.length} 个作品，请勾选要提取的内容。`, "ok")
          renderResults()
          saveCreatorState()
          if (extractButton) extractButton.disabled = false
        }
      } catch (error) {
        setStatusText(error.message || "扫描失败", "bad")
      } finally {
        setBusy(scanButton, false)
      }
    })

    // 渲染作品列表
    const renderResults = () => {
      if (!results) return
      results.hidden = false

      const itemStats = (item) => [
        ["点赞", item.like_count],
        ["评论", item.comment_count],
        ["收藏", item.collect_count],
        ["转发", item.share_count],
      ].map(([label, value]) => {
        const text = numberText(value)
        return text ? `<span>${label} ${escapeHtml(text)}</span>` : ""
      }).join("")

      const itemCard = (item) => `
        <div class="creator-work-item" data-item-id="${escapeHtml(item.id)}">
          <input class="creator-work-checkbox" type="checkbox" data-item-checkbox value="${escapeHtml(item.id)}" aria-label="选择 ${escapeHtml(item.title)}">
          <div class="creator-work-body">
            <div class="creator-work-title-row">
              <strong class="creator-work-title">${escapeHtml(cleanCreatorWorkTitle(item.title) || item.title)}</strong>
              ${item.bucket === "both" ? '<span class="creator-work-badge">最新 + 高赞</span>' : item.bucket === "top_liked" ? '<span class="creator-work-badge">高赞</span>' : '<span class="creator-work-badge">最新</span>'}
            </div>
            <div class="creator-work-stats">
              ${itemStats(item)}
              ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">查看原文</a>` : ""}
            </div>
          </div>
        </div>
      `

      const topLikedItems = scannedItems
        .filter((item) => item.bucket === "top_liked" || item.bucket === "both")
        .sort((a, b) => Number(b.like_count || 0) - Number(a.like_count || 0))
      const latestItems = scannedItems.filter((item) => item.bucket === "latest" || item.bucket === "both")
      results.innerHTML = `
        ${renderCreatorProfile()}
        <section class="creator-work-section">
          <div class="creator-work-section-head">
            <h3>高赞 10 条</h3>
            <button class="btn secondary small" type="button" data-select-group="top_liked">全选</button>
          </div>
          <div class="creator-work-list">${topLikedItems.map(itemCard).join("") || '<p class="muted">暂无高赞作品</p>'}</div>
        </section>
        <section class="creator-work-section">
          <div class="creator-work-section-head">
            <h3>最新 10 条</h3>
            <button class="btn secondary small" type="button" data-select-group="latest">全选</button>
          </div>
          <div class="creator-work-list">${latestItems.map(itemCard).join("") || '<p class="muted">暂无最新作品</p>'}</div>
        </section>
      `

      const syncCreatorCheckboxes = (itemId, checked) => {
        results.querySelectorAll("[data-item-checkbox]").forEach((checkbox) => {
          if (checkbox.value === itemId) checkbox.checked = checked
        })
      }

      const selectCreatorGroup = (group) => {
        const groupItems = group === "top_liked" ? topLikedItems : latestItems
        selectedItemIds = Array.from(new Set(selectedItemIds.concat(groupItems.map((item) => String(item.id)))))
        results.querySelectorAll("[data-item-checkbox]").forEach((checkbox) => {
          checkbox.checked = selectedItemIds.includes(checkbox.value)
        })
        saveCreatorState()
      }

      results.querySelectorAll("[data-select-group]").forEach((button) => {
        button.addEventListener("click", () => selectCreatorGroup(button.dataset.selectGroup))
      })

      // 绑定复选框事件
      results.querySelectorAll("[data-item-checkbox]").forEach((checkbox) => {
        checkbox.checked = selectedItemIds.includes(checkbox.value)
        checkbox.addEventListener("change", () => {
          syncCreatorCheckboxes(checkbox.value, checkbox.checked)
          selectedItemIds = Array.from(new Set(
            Array.from(results.querySelectorAll("[data-item-checkbox]:checked")).map((cb) => cb.value)
          ))
          saveCreatorState()
        })
      })
    }

    const runCreatorExtraction = async (existingOverlay = null) => {
      if (!selectedCandidateIds.length) return null
      const extractEndpoint = currentPlatform === "xiaohongshu" ? "/api/xiaohongshu/diandian/extract-selected" : "/api/doubao/extract-selected"
      const payload = { candidate_ids: selectedCandidateIds, per_item_timeout_seconds: 240, async_job: true }
      if (currentJobId) payload.job_id = currentJobId
      const overlay = existingOverlay || createExtractOverlay("作品内容正在提取")
      renderExtractOverlay(overlay, { total: selectedCandidateIds.length, items: selectedCandidateIds.map((candidateId) => ({ candidate_id: candidateId, status: "pending" })) })
      const response = await apiPost(extractEndpoint, payload)
      currentJobId = response.job_id || currentJobId
      saveCreatorState()
      const finalStatus = await pollExtractJob(currentJobId, { overlay })
      const payloadStatus = finalStatus || response
      if (payloadStatus.status === "paused") {
        const remaining = response.pending_remaining != null ? response.pending_remaining : "若干"
        const message = payloadStatus.message || response.message || "检测到人机验证，已暂停。"
        setStatusText(`${message} 本轮已入库 ${payloadStatus.success_count || 0} 条，剩余 ${remaining} 条。`, "bad")
        showExtractPauseDialog({
          message,
          onResume: async () => {
            await runCreatorExtraction(overlay)
          },
        })
        return payloadStatus
      }
      if (payloadStatus.status === "error") throw new Error(payloadStatus.error || "提取任务失败")
      currentJobId = null
      setStatusText(`提取完成：成功 ${payloadStatus.success_count || 0} 条，失败 ${payloadStatus.failed_count || 0} 条。`, "ok")
      selectedItemIds = []
      if (results) {
        results.querySelectorAll("[data-item-checkbox]").forEach((cb) => {
          cb.checked = false
        })
      }
      saveCreatorState()
      return payloadStatus
    }

    // 提取选中的作品
    extractButton?.addEventListener("click", async () => {
      if (selectedItemIds.length === 0) {
        setStatusText("请先勾选要提取的作品", "bad")
        return
      }

      setBusy(extractButton, true, "提取中...")
      setStatusText(`正在提取 ${selectedItemIds.length} 个作品...`)

      try {
        const selectedItems = scannedItems.filter((item) => selectedItemIds.includes(String(item.id)))
        const prepared = await apiPost("/api/creator/prepare-selected", {
          platform: currentPlatform,
          creator: currentCreator || {},
          selected_items: selectedItems,
        })
        selectedCandidateIds = prepared.candidate_ids || []
        currentJobId = null
        saveCreatorState()
        if (!selectedCandidateIds.length) {
          setStatusText("没有可提取的作品，请重新扫描后再试。", "bad")
          return
        }
        await runCreatorExtraction()
      } catch (error) {
        setStatusText(error.message || "提取失败", "bad")
      } finally {
        setBusy(extractButton, false)
      }
    })

  }
  document.querySelectorAll("[data-creator-distill-panel]").forEach(initCreatorDistillPanel)

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
  // Check for pending feedback on page load (user opens the app)
  fetch('/api/push/pending-feedback').then(function(r){ return r.json(); }).then(function(items){
    if(!items.length) return;
    var item = items[0];
    showFeedbackBanner(item);
  }).catch(function(){});

  function showFeedbackBanner(item) {
    var banner = document.createElement('div');
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#fff;border-bottom:2px solid #215f52;padding:16px 24px;box-shadow:0 4px 16px rgba(0,0,0,0.1);display:flex;align-items:center;gap:16px;';
    banner.innerHTML = '<div style="flex:1;"><strong>这次推送对你有帮助吗？</strong><div style="font-size:.85rem;color:#666;margin-top:4px;">【' + item.category + '】' + item.title + '</div></div><button class="fb-like" style="border:none;background:#215f52;color:#fff;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:.9rem;">👍 有帮助</button><button class="fb-unlike" style="border:none;background:#eee;color:#333;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:.9rem;">👎 不相关</button>';
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
  setInterval(async () => {
    try {
      const res = await fetch('/api/push/items');
      const items = await res.json();
      if (!items.length) return;
      items.forEach(item => {
        new Notification('StarMind 知识推送', {
          body: '【' + item.category + '】' + item.title + '\n' + item.summary,
          tag: 'push-' + item.push_id
        });
      });
    } catch(e) {}
  }, 60000);
})();

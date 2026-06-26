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

  document.querySelectorAll("[data-source-filter]").forEach((root) => {
    const platform = root.dataset.platform
    const homepageInput = document.querySelector("[data-source-homepage-input]")
    const limitSelect = root.querySelector("[data-filter-limit]")
    const scanButton = root.querySelector("[data-filter-scan]")
    const classifyButton = root.querySelector("[data-filter-classify]")
    const extractButton = root.querySelector("[data-filter-extract]")
    const status = root.querySelector("[data-filter-status]")
    const summary = root.querySelector("[data-filter-summary]")
    const results = root.querySelector("[data-filter-results]")
    let scannedItems = []
    let classifiedItems = []
    let selectedCandidateIds = []
    let lastGroups = []
    const resumablePlatforms = new Set(["douyin", "xiaohongshu", "bilibili"])
    const canResume = resumablePlatforms.has(platform)
    const stateKey = `starmind.batchTitleFilter.${platform}`

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
      results.innerHTML = items.map((item) => `
        <div class="filter-item preview-only">
          <span><strong>${escapeHtml(item.title || item.url)}</strong><small>${escapeHtml(item.author || item.platform || "")}</small><a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">打开源网页</a></span>
        </div>
      `).join("")
    }

    const renderGroups = (groups) => {
      lastGroups = groups || []
      if (!results) return
      results.hidden = false
      if (!groups.length) {
        results.innerHTML = '<div class="empty-state">没有可展示的分类结果。</div>'
        return
      }
      results.innerHTML = groups.map((group, groupIndex) => {
        const groupKey = `${group.usefulness}-${group.subcategory}-${groupIndex}`
        const items = group.items || []
        return `
          <section class="filter-group" data-filter-group="${escapeHtml(groupKey)}">
            <div class="filter-group-head">
              <label class="filter-group-check">
                <input type="checkbox" data-group-toggle checked>
                <span>${group.usefulness === "useful" ? "有用" : "没用"} · ${escapeHtml(group.subcategory)}</span>
              </label>
              <span class="status-chip ${group.usefulness === "useful" ? "success" : "warning"}">${group.count} 条</span>
            </div>
            <div class="filter-item-list">
              ${items.map((item, itemIndex) => `
                <label class="filter-item">
                  <input type="checkbox" data-item-check data-item-index="${classifiedItems.indexOf(item)}" ${group.usefulness === "useful" ? "checked" : ""}>
                  <span>
                    <strong>${escapeHtml(item.title || item.url)}</strong>
                    <small>${escapeHtml(item.author || item.platform || "未知作者")} · ${escapeHtml(item.reason || "")}</small>
                    <a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">打开源网页</a>
                  </span>
                </label>
              `).join("")}
            </div>
          </section>
        `
      }).join("")
      results.querySelectorAll("[data-group-toggle]").forEach((checkbox) => {
        checkbox.addEventListener("change", () => {
          const group = checkbox.closest("[data-filter-group]")
          group?.querySelectorAll("[data-item-check]").forEach((itemCheck) => {
            itemCheck.checked = checkbox.checked
          })
        })
      })
    }

    const restoreState = () => {
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
      } else if (saved.stage === "classified" || saved.stage === "prepared") {
        renderGroups(lastGroups)
        classifyButton.disabled = scannedItems.length === 0
        extractButton.disabled = classifiedItems.length === 0 && selectedCandidateIds.length === 0
      } else if (saved.stage === "completed") {
        if (lastGroups.length) renderGroups(lastGroups)
        else if (scannedItems.length) renderScannedPreview(scannedItems)
        classifyButton.disabled = scannedItems.length === 0
        extractButton.disabled = true
      } else {
        clearSavedState()
      }
    }

    restoreState()

    scanButton?.addEventListener("click", async () => {
      setBusy(scanButton, true, "扫描中...")
      setStatus("正在通过浏览器读取收藏标题，请不要关闭浏览器。")
      try {
        const currentHomepageUrl = homepageInput?.value?.trim() || root.dataset.homepageUrl || ""
        const body = await apiPost("/api/sync/scan-titles", { platform, limit: limitSelect?.value || 10, homepage_url: currentHomepageUrl })
        scannedItems = body.items || []
        classifiedItems = []
        selectedCandidateIds = []
        if (summary) {
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
      setStatus(isXiaohongshu
        ? `已选择 ${selected.length} 条。正在创建候选并发送到小红书点点，单条可能等待数分钟。`
        : `已选择 ${selected.length} 条。正在创建候选并发送到豆包，单条可能等待数分钟。`)
      try {
        const prepared = await apiPost("/api/sync/prepare-selected", { platform, selected_items: selected, skipped_items: skipped })
        const preparedIds = prepared.candidate_ids || []
        if (preparedIds.length) selectedCandidateIds = preparedIds
        saveState("prepared")
        if (!selectedCandidateIds.length) {
          setStatus("没有可提取的候选。可能这些内容已经准备过或已入库，请重新扫描/分类后再试。", "bad")
          return
        }
        const extractEndpoint = isXiaohongshu ? "/api/xiaohongshu/diandian/extract-selected" : "/api/doubao/extract-selected"
        const extracted = await apiPost(extractEndpoint, { candidate_ids: selectedCandidateIds, per_item_timeout_seconds: 240 })
        setStatus(`完成：成功入库 ${extracted.success_count || 0} 条，失败 ${extracted.failed_count || 0} 条。`, "ok")
        if (summary) summary.textContent = `RawSource 已写入，可前往“原始资料”查看。候选 ID：${selectedCandidateIds.join(", ")}`
        saveState("completed")
      } catch (error) {
        if (error.code === "xiaohongshu_diandian_not_ready") {
          setStatus("小红书点点页面未就绪。请确认浏览器仍登录小红书，并打开 https://www.xiaohongshu.com/ai_chat 后重试。", "bad")
        } else if (error.code === "doubao_login_required") {
          setStatus("需要先登录豆包。系统已打开豆包页面，请在浏览器完成登录；如果豆包没有主动弹窗，请在豆包页面发送任意一句话触发登录弹窗，登录完成后回到这里重试。豆包登录入口：https://www.doubao.com", "bad")
        } else {
          setStatus(error.message || (isXiaohongshu ? "小红书点点提取失败" : "豆包提取失败"), "bad")
        }
      } finally {
        setBusy(extractButton, false)
      }
    })
  })

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", (event) => {
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
})()

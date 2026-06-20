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
    const url = picker.querySelector("[data-provider-url]")
    const key = picker.querySelector("[data-provider-key]")
    const modelInput = picker.closest("form")?.querySelector("[data-model-input]")

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
      if (url) url.textContent = provider.base_url || "需要你在自定义接口里填写 Base URL"
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

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
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

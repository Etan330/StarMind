(function () {
  const grid = document.getElementById("workbench-grid")
  const saveButton = document.getElementById("save-layout")
  const resetButton = document.getElementById("reset-layout")

  if (!grid) return

  const parseJson = (value, fallback) => {
    try {
      return JSON.parse(value)
    } catch (_error) {
      return fallback
    }
  }

  const stats = parseJson(grid.dataset.stats || "{}", {})

  const defaultLayout = [
    { id: "today_sync", position: 0, size: "medium", settings: { show_counts: true } },
    { id: "pending_items", position: 1, size: "medium", settings: { limit: 5 } },
    { id: "recent_sources", position: 2, size: "medium", settings: { limit: 5 } },
    { id: "knowledge_topics", position: 3, size: "medium", settings: { limit: 6 } },
  ]

  const moduleIcons = {
    today_sync: "01",
    pending_items: "02",
    recent_sources: "03",
    knowledge_topics: "04",
  }

  const moduleContent = {
    today_sync: `
      <p>今天先处理新进来的收藏，不让它们继续堆在收藏夹里。</p>
      <div class="module-stat-grid">
        <div class="module-stat"><span>新增</span><strong>${stats.intake_total || 0}</strong></div>
        <div class="module-stat"><span>建议处理</span><strong>${stats.pending_count || 0}</strong></div>
      </div>
    `,
    pending_items: `
      <p>只有少量边界内容需要你确认，确认后才会进入知识库。</p>
      <a class="btn primary full" href="/ui/pending">去处理</a>
    `,
    recent_sources: `
      <p>这些收藏已经进入原始资料库，可以继续沉淀成页面或 SOP。</p>
      <ul class="module-mini-list">
        <li>AI Agent 行业应用趋势报告</li>
        <li>自动化流程设计最佳实践</li>
        <li>知识管理方法论与实践</li>
      </ul>
    `,
    knowledge_topics: `
      <p>反复出现的主题会在这里浮出来，提醒你可以整理成专项资料。</p>
      <div class="topic-cloud">
        <span class="status-chip primary">AI Agent</span>
        <span class="status-chip primary">自动化</span>
        <span class="status-chip primary">知识管理</span>
      </div>
    `,
  }

  const moduleDefs = parseJson(grid.dataset.modules || "[]", [])
  const moduleMap = new Map(moduleDefs.map((module) => [module.id, module]))
  let layout = parseJson(grid.dataset.layout || "{}", { modules: defaultLayout }).modules || defaultLayout

  const normalizeLayout = () => {
    const allowed = new Set(defaultLayout.map((module) => module.id))
    const existing = layout
      .filter((module) => module && allowed.has(module.id) && moduleMap.has(module.id))
      .map((module, index) => ({
        id: module.id,
        position: index,
        size: "medium",
        settings: module.settings || {},
      }))
    const seen = new Set(existing.map((module) => module.id))
    defaultLayout.forEach((module) => {
      if (!seen.has(module.id)) existing.push({ ...module, settings: { ...module.settings } })
    })
    layout = existing.map((module, index) => ({ ...module, position: index }))
  }

  const setButtonState = (text) => {
    if (!saveButton) return
    saveButton.textContent = text
    window.clearTimeout(saveButton._restoreTimer)
    saveButton._restoreTimer = window.setTimeout(() => {
      saveButton.textContent = "保存排序"
    }, 1400)
  }

  const saveLayout = async () => {
    normalizeLayout()
    try {
      const response = await fetch("/workbench/layout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ modules: layout }),
      })
      if (!response.ok) throw new Error("save failed")
      setButtonState("已保存")
    } catch (_error) {
      setButtonState("保存失败")
    }
  }

  const render = () => {
    normalizeLayout()
    grid.innerHTML = layout
      .map((module, index) => {
        const def = moduleMap.get(module.id)
        return `
          <article class="workbench-module digest-module" draggable="true" data-index="${index}">
            <div class="module-top">
              <span class="digest-number">${moduleIcons[module.id]}</span>
              <strong>${def ? def.name : module.id}</strong>
              <div class="module-actions">
                <button class="icon-btn" type="button" data-action="up" title="上移">↑</button>
                <button class="icon-btn" type="button" data-action="down" title="下移">↓</button>
              </div>
            </div>
            ${moduleContent[module.id] || ""}
          </article>
        `
      })
      .join("")
  }

  const moveModule = (from, to) => {
    if (from === to || from < 0 || to < 0 || from >= layout.length || to >= layout.length) return
    const [module] = layout.splice(from, 1)
    layout.splice(to, 0, module)
    render()
    saveLayout()
  }

  grid.addEventListener("dragstart", (event) => {
    const card = event.target.closest(".workbench-module")
    if (!card) return
    event.dataTransfer.effectAllowed = "move"
    event.dataTransfer.setData("text/plain", card.dataset.index)
  })

  grid.addEventListener("dragover", (event) => {
    event.preventDefault()
    grid.classList.add("is-over")
  })

  grid.addEventListener("dragleave", () => grid.classList.remove("is-over"))

  grid.addEventListener("drop", (event) => {
    event.preventDefault()
    grid.classList.remove("is-over")
    const from = Number(event.dataTransfer.getData("text/plain"))
    const target = event.target.closest(".workbench-module")
    const to = target ? Number(target.dataset.index) : layout.length - 1
    moveModule(from, to)
  })

  grid.addEventListener("click", (event) => {
    const card = event.target.closest(".workbench-module")
    if (!card) return
    const index = Number(card.dataset.index)
    if (event.target.dataset.action === "up") moveModule(index, index - 1)
    if (event.target.dataset.action === "down") moveModule(index, index + 1)
  })

  saveButton?.addEventListener("click", saveLayout)
  resetButton?.addEventListener("click", () => {
    layout = defaultLayout.map((module) => ({ ...module, settings: { ...module.settings } }))
    render()
    saveLayout()
  })

  render()
})()

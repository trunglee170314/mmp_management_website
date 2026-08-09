(() => {
  const openDialog = (dialog) => {
    if (!dialog) return;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  };

  const closeDialog = (dialog) => {
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  };

  document.addEventListener("click", (event) => {
    document.querySelectorAll("details.timeline-settings-menu[open]").forEach((menu) => {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });

    const openButton = event.target.closest("[data-dialog-open]");
    if (openButton) {
      openButton.closest("details.timeline-settings-menu")?.removeAttribute("open");
      openDialog(document.getElementById(openButton.dataset.dialogOpen));
      return;
    }

    const closeButton = event.target.closest("[data-dialog-close]");
    if (closeButton) {
      closeDialog(closeButton.closest("dialog"));
      return;
    }

    const groupToggle = event.target.closest("[data-group-toggle]");
    if (groupToggle) {
      const section = groupToggle.closest("[data-timeline-section]");
      const body = section?.querySelector("[data-group-body]");
      if (!body) return;
      const collapsed = !body.hidden;
      body.hidden = collapsed;
      groupToggle.setAttribute("aria-expanded", String(!collapsed));
      const groupName = groupToggle.querySelector("span")?.textContent?.trim() || "Timeline";
      groupToggle.setAttribute("aria-label", `${collapsed ? "Expand" : "Collapse"} ${groupName} group`);
      section.classList.toggle("is-collapsed", collapsed);
      requestAnimationFrame(updateTimelineViewportHeight);
      return;
    }

    const scheduleButton = event.target.closest("[data-task-schedule]");
    if (scheduleButton) {
      const dialog = document.getElementById("timeline-task-dialog");
      const form = dialog?.querySelector("[data-task-schedule-form]");
      if (!dialog || !form) return;
      form.action = scheduleButton.dataset.action;
      dialog.querySelector("[data-task-dialog-title]").textContent = scheduleButton.dataset.title;
      dialog.querySelector("[data-task-edit]").href = scheduleButton.dataset.editAction;
      dialog.querySelector("[data-task-start]").value = scheduleButton.dataset.start || "";
      dialog.querySelector("[data-task-due]").value = scheduleButton.dataset.due || "";
      dialog.querySelector("[data-task-group]").value = scheduleButton.dataset.group || "";
      openDialog(dialog);
    }
  });

  document.querySelectorAll("dialog.timeline-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      const rect = dialog.getBoundingClientRect();
      const inside = event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (!inside) closeDialog(dialog);
    });
  });

  const scrollToToday = (behavior = "auto") => {
    const viewport = document.querySelector("[data-timeline-scroll]");
    const today = viewport?.querySelector(".timeline-day-label.is-today");
    if (!viewport || !today) return;
    const taskColumn = Number.parseFloat(getComputedStyle(viewport).getPropertyValue("--task-column")) || 0;
    const target = taskColumn + today.offsetLeft + (today.offsetWidth / 2) - (viewport.clientWidth / 2);
    viewport.scrollTo({left: Math.max(0, target), behavior});
  };

  const viewport = document.querySelector("[data-timeline-scroll]");
  const yearGrid = document.querySelector("[data-timeline-year-grid]");
  const yearLabels = Array.from(document.querySelectorAll("[data-timeline-year-label]"));
  const activeYear = document.querySelector("[data-timeline-active-year]");
  const rowLimitSelect = document.querySelector("[data-timeline-row-limit]");
  const zoomOutButton = document.querySelector("[data-timeline-zoom-out]");
  const zoomInButton = document.querySelector("[data-timeline-zoom-in]");
  const zoomLabel = document.querySelector("[data-timeline-zoom-label]");
  const defaultDayWidth = 42;
  const minimumDayWidth = 16;
  const maximumDayWidth = 72;
  const zoomStep = 4;
  const zoomStorageKey = "mmp-timeline-day-width";
  const rowLimitStorageKey = "mmp-timeline-visible-rows";

  function updateTimelineViewportHeight() {
    if (!viewport || !rowLimitSelect) return;
    const requestedRows = rowLimitSelect.value;
    const visibleRows = Array.from(viewport.querySelectorAll(".timeline-task-row"))
      .filter((row) => row.getClientRects().length > 0);
    const rowCount = Number.parseInt(requestedRows, 10);
    if (requestedRows === "all" || !Number.isFinite(rowCount) || visibleRows.length <= rowCount) {
      viewport.classList.remove("has-row-limit");
      viewport.style.removeProperty("max-height");
      return;
    }
    const finalVisibleRow = visibleRows[rowCount - 1];
    const viewportHeight = finalVisibleRow.offsetTop + finalVisibleRow.offsetHeight + 18;
    viewport.style.maxHeight = `${viewportHeight}px`;
    viewport.classList.add("has-row-limit");
  }

  let activeYearFrame = 0;
  const updateActiveYear = () => {
    activeYearFrame = 0;
    if (!viewport || !yearGrid || !activeYear || !yearLabels.length) return;
    const scrollLeft = viewport.scrollLeft;
    let currentYear = yearLabels[0];
    yearLabels.forEach((label) => {
      if (label.offsetLeft <= scrollLeft + 1) currentYear = label;
    });
    activeYear.textContent = currentYear.dataset.year;
    const maximumOffset = Math.max(0, yearGrid.scrollWidth - activeYear.offsetWidth);
    activeYear.style.transform = `translateX(${Math.min(scrollLeft, maximumOffset)}px)`;
  };

  const scheduleActiveYearUpdate = () => {
    if (activeYearFrame) return;
    activeYearFrame = requestAnimationFrame(updateActiveYear);
  };

  const dayWidth = () => Number.parseFloat(getComputedStyle(viewport).getPropertyValue("--day-width")) || defaultDayWidth;
  const taskColumnWidth = () => Number.parseFloat(getComputedStyle(viewport).getPropertyValue("--task-column")) || 0;
  const clampZoom = (value) => Math.min(maximumDayWidth, Math.max(minimumDayWidth, value));

  const updateZoomControls = (value) => {
    if (zoomLabel) zoomLabel.textContent = `${Math.round((value / defaultDayWidth) * 100)}%`;
    if (zoomOutButton) zoomOutButton.disabled = value <= minimumDayWidth;
    if (zoomInButton) zoomInButton.disabled = value >= maximumDayWidth;
  };

  const setZoom = (requestedWidth, {preserveCenter = true, remember = true} = {}) => {
    if (!viewport) return;
    const oldWidth = dayWidth();
    const taskColumn = taskColumnWidth();
    const centerDay = Math.max(0, (viewport.scrollLeft + (viewport.clientWidth / 2) - taskColumn) / oldWidth);
    const newWidth = clampZoom(requestedWidth);
    const zoomRatio = newWidth / defaultDayWidth;
    viewport.style.setProperty("--day-width", `${newWidth}px`);
    viewport.style.setProperty("--timeline-bar-height", `${Math.min(38, Math.max(18, 24 * zoomRatio))}px`);
    viewport.style.setProperty("--timeline-bar-font-size", `${Math.min(15, Math.max(8, 11 * zoomRatio))}px`);
    viewport.style.setProperty("--timeline-bar-padding", `${Math.min(10, Math.max(3, 8 * zoomRatio))}px`);
    viewport.style.setProperty("--timeline-row-height", `${Math.min(78, Math.max(48, 65 * Math.sqrt(zoomRatio)))}px`);
    const headerScale = Math.sqrt(zoomRatio);
    const yearHeight = Math.min(30, Math.max(18, 26 * headerScale));
    const monthHeight = Math.min(32, Math.max(18, 28 * headerScale));
    const dayHeaderHeight = Math.min(52, Math.max(28, 48 * headerScale));
    viewport.style.setProperty("--timeline-year-height", `${yearHeight}px`);
    viewport.style.setProperty("--timeline-month-height", `${monthHeight}px`);
    viewport.style.setProperty("--timeline-day-header-height", `${dayHeaderHeight}px`);
    viewport.style.setProperty("--timeline-header-height", `${yearHeight + monthHeight + dayHeaderHeight}px`);
    viewport.style.setProperty("--timeline-year-font-size", `${Math.min(12, Math.max(8, 11 * headerScale))}px`);
    viewport.style.setProperty("--timeline-month-font-size", `${Math.min(11, Math.max(8, 10 * headerScale))}px`);
    viewport.style.setProperty("--timeline-day-font-size", `${Math.min(13, Math.max(8, 12 * headerScale))}px`);
    updateZoomControls(newWidth);
    scheduleActiveYearUpdate();
    if (remember) {
      try { localStorage.setItem(zoomStorageKey, String(newWidth)); } catch (_) { /* Storage can be disabled. */ }
    }
    if (preserveCenter) {
      requestAnimationFrame(() => {
        viewport.scrollLeft = Math.max(0, taskColumn + (centerDay * newWidth) - (viewport.clientWidth / 2));
      });
    }
  };

  if (viewport) {
    let savedZoom = defaultDayWidth;
    try { savedZoom = Number.parseFloat(localStorage.getItem(zoomStorageKey)) || defaultDayWidth; } catch (_) { /* Storage can be disabled. */ }
    setZoom(savedZoom, {preserveCenter: false, remember: false});

    zoomOutButton?.addEventListener("click", () => setZoom(dayWidth() - zoomStep));
    zoomInButton?.addEventListener("click", () => setZoom(dayWidth() + zoomStep));
    viewport.addEventListener("scroll", scheduleActiveYearUpdate, {passive: true});
    viewport.addEventListener("wheel", (event) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      setZoom(dayWidth() + (event.deltaY < 0 ? zoomStep : -zoomStep));
    }, {passive: false});
  }

  if (rowLimitSelect) {
    try {
      const savedRowLimit = localStorage.getItem(rowLimitStorageKey);
      if (["5", "10", "15", "20", "all"].includes(savedRowLimit)) rowLimitSelect.value = savedRowLimit;
    } catch (_) { /* Storage can be disabled. */ }
    rowLimitSelect.addEventListener("change", () => {
      try { localStorage.setItem(rowLimitStorageKey, rowLimitSelect.value); } catch (_) { /* Storage can be disabled. */ }
      if (viewport) viewport.scrollTop = 0;
      updateTimelineViewportHeight();
    });
    requestAnimationFrame(updateTimelineViewportHeight);
  }

  window.addEventListener("resize", scheduleActiveYearUpdate);
  window.addEventListener("resize", updateTimelineViewportHeight);
  scheduleActiveYearUpdate();

  const todayButton = document.querySelector("[data-scroll-today]");
  if (todayButton && document.querySelector(".timeline-day-label.is-today")) {
    todayButton.addEventListener("click", (event) => {
      event.preventDefault();
      scrollToToday("smooth");
    });
  }

  requestAnimationFrame(() => scrollToToday());
})();

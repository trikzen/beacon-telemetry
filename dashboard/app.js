(function () {
  "use strict";

  // ---- settings ----
  const MAX_FEED_ROWS = 50;     // rows shown in the live feed
  const HISTORY_LIMIT = 200;    // events fetched when the page opens
  const MAX_STORED = 1000;      // events kept in memory
  const MAX_POINTS = 100;       // points per line on a chart
  const MAX_CATEGORIES = 8;     // text fields with more distinct values get no chart
  const MIN_WINDOW_MS = 10000;  // shortest time span a line chart will show
  const RECONNECT_MS = 2000;
  const PALETTE = [
    "#3b82f6", "#f59e0b", "#10b981", "#ef4444",
    "#8b5cf6", "#14b8a6", "#ec4899", "#84cc16",
  ];

  // ---- page elements ----
  const $ = (id) => document.getElementById(id);
  const statusEl = $("status");
  const themeToggle = $("themeToggle");
  const filterEl = $("deviceFilter");
  const deviceListEl = $("deviceList");
  const noDevicesEl = $("noDevices");
  const chartsEl = $("charts");
  const chartsEmptyEl = $("chartsEmpty");
  const chartsNoticeEl = $("chartsNotice");
  const feedMetaEl = $("feedMeta");
  const rowsEl = $("rows");
  const emptyFeedEl = $("emptyFeed");

  const hasChart = typeof Chart !== "undefined";

  // ---- state ----
  const eventsById = new Map();
  let events = [];                    // sorted oldest to newest (by id)
  let selectedDevice = "all";
  const deviceColors = new Map();     // device id -> colour
  const deviceUI = new Map();         // device id -> its elements in the sidebar
  const chartUI = new Map();          // chart key -> { chart, card, kind }
  let deviceIdsKey = "";

  function colorFor(deviceId) {
    if (!deviceColors.has(deviceId)) {
      deviceColors.set(deviceId, PALETTE[deviceColors.size % PALETTE.length]);
    }
    return deviceColors.get(deviceId);
  }

  // ---- data ----
  function ingest(list) {
    let outOfOrder = false;
    for (const e of list) {
      if (!e || typeof e.id !== "number" || eventsById.has(e.id)) continue;  // skip duplicates
      eventsById.set(e.id, e);
      if (events.length && events[events.length - 1].id > e.id) outOfOrder = true;
      events.push(e);
      colorFor(e.device_id);
    }
    if (outOfOrder) events.sort((a, b) => a.id - b.id);
    if (events.length > MAX_STORED) {
      const dropped = events.splice(0, events.length - MAX_STORED);
      dropped.forEach((e) => eventsById.delete(e.id));
    }
  }

  function visibleEvents() {
    return selectedDevice === "all"
      ? events
      : events.filter((e) => e.device_id === selectedDevice);
  }

  function seenMs(e) {
    const t = Date.parse(e.received_at);
    return Number.isNaN(t) ? Date.parse(e.timestamp) : t;
  }

  function ago(ms) {
    const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 5) return "just now";
    if (s < 60) return s + "s ago";
    const m = Math.floor(s / 60);
    if (m < 60) return m + "m ago";
    const h = Math.floor(m / 60);
    if (h < 24) return h + "h ago";
    return Math.floor(h / 24) + "d ago";
  }

  function formatClock(ms) {
    return new Date(ms).toLocaleTimeString();
  }

  // ---- rendering: rendering is batched, so a burst of events draws only once ----
  let renderScheduled = false;
  const nextFrame = window.requestAnimationFrame || ((fn) => setTimeout(fn, 16));

  function scheduleRender() {
    if (renderScheduled) return;
    renderScheduled = true;
    nextFrame(() => {
      renderScheduled = false;
      render();
    });
  }

  function render() {
    renderDevices();
    renderFeed();
    renderCharts();
  }

  function setFilter(value) {
    selectedDevice = value;
    filterEl.value = value;
    scheduleRender();
  }

  // Devices (sidebar + filter dropdown)
  function renderDevices() {
    const info = new Map();
    for (const e of events) {
      let d = info.get(e.device_id);
      if (!d) {
        d = { count: 0, last: 0 };
        info.set(e.device_id, d);
      }
      d.count += 1;
      d.last = Math.max(d.last, seenMs(e) || 0);
    }

    const ids = [...info.keys()].sort();
    noDevicesEl.hidden = ids.length > 0;

    for (const id of ids) {
      let ui = deviceUI.get(id);
      if (!ui) {
        const li = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "device";
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.backgroundColor = colorFor(id);
        const text = document.createElement("span");
        const nameEl = document.createElement("span");
        nameEl.className = "device-name";
        nameEl.textContent = id;          // textContent: device data is untrusted
        const metaEl = document.createElement("span");
        metaEl.className = "device-meta";
        text.append(nameEl, metaEl);
        button.append(swatch, text);
        button.addEventListener("click", () => {
          setFilter(selectedDevice === id ? "all" : id);
        });
        li.appendChild(button);
        ui = { li, button, metaEl };
        deviceUI.set(id, ui);
      }
      const d = info.get(id);
      ui.metaEl.textContent =
        d.count + (d.count === 1 ? " event" : " events") + " · " + ago(d.last);
      ui.button.classList.toggle("active", selectedDevice === id);
    }

    // Rebuild the list and dropdown only when the set of devices changes.
    const key = ids.join("|");
    if (key !== deviceIdsKey) {
      deviceIdsKey = key;
      deviceListEl.replaceChildren(...ids.map((id) => deviceUI.get(id).li));
      const options = [new Option("All devices", "all"), ...ids.map((id) => new Option(id, id))];
      filterEl.replaceChildren(...options);
      filterEl.value = selectedDevice;
    }
  }

  // Live feed table
  function rowFor(e) {
    const tr = document.createElement("tr");
    const values = [
      formatClock(Date.parse(e.timestamp)),
      e.device_id,
      e.sequence,
      e.event_type,
      JSON.stringify(e.data),
    ];
    values.forEach((value, i) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (i === 4) td.className = "data";
      tr.appendChild(td);
    });
    return tr;
  }

  function renderFeed() {
    const list = visibleEvents();
    const rows = [];
    for (let i = list.length - 1; i >= 0 && rows.length < MAX_FEED_ROWS; i--) {
      rows.push(rowFor(list[i]));
    }
    rowsEl.replaceChildren(...rows);
    emptyFeedEl.hidden = list.length > 0;
    feedMetaEl.textContent = list.length
      ? "Showing the latest " + rows.length + " of " + list.length + " events loaded."
      : "";
  }

  // Charts: the type of chart is chosen from the type of each field in the data.
  //   number field          -> line chart (one line per device)
  //   text field, few values -> bar chart of counts (for example "state")
  function collectSpecs(list) {
    const specs = new Map();
    for (const e of list) {
      for (const [field, value] of Object.entries(e.data || {})) {
        let kind = null;
        if (typeof value === "number" && Number.isFinite(value)) kind = "line";
        else if (typeof value === "string" || typeof value === "boolean") kind = "bar";
        if (!kind) continue;

        const key = e.event_type + "|" + field + "|" + kind;
        let spec = specs.get(key);
        if (!spec) {
          spec = {
            key,
            kind,
            title: e.event_type + " · " + field,
            devices: new Map(),
            counts: new Map(),
          };
          specs.set(key, spec);
        }

        if (kind === "line") {
          const x = Date.parse(e.timestamp);
          if (Number.isNaN(x)) continue;
          if (!spec.devices.has(e.device_id)) spec.devices.set(e.device_id, []);
          spec.devices.get(e.device_id).push({ x, y: value });
        } else {
          const label = String(value);
          spec.counts.set(label, (spec.counts.get(label) || 0) + 1);
        }
      }
    }

    const result = [];
    for (const spec of specs.values()) {
      if (spec.kind === "bar" && spec.counts.size > MAX_CATEGORIES) continue;
      for (const [id, points] of spec.devices) {
        points.sort((a, b) => a.x - b.x);              // draw each line left to right
        spec.devices.set(id, points.slice(-MAX_POINTS));
      }
      result.push(spec);
    }
    return result.sort((a, b) => a.key.localeCompare(b.key));
  }

  function themeColors() {
    const styles = getComputedStyle(document.documentElement);
    return {
      text: styles.getPropertyValue("--muted").trim() || "#666",
      grid: styles.getPropertyValue("--grid").trim() || "rgba(0,0,0,0.1)",
    };
  }

  function applyChartTheme(chart) {
    const colors = themeColors();
    for (const axis of Object.values(chart.options.scales)) {
      axis.ticks.color = colors.text;
      axis.grid.color = colors.grid;
    }
    chart.options.plugins.legend.labels.color = colors.text;
  }

  function chartOptions(kind) {
    const options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { labels: {} }, tooltip: {} },
      scales: { x: { ticks: {}, grid: {} }, y: { ticks: {}, grid: {} } },
    };
    if (kind === "line") {
      options.interaction = { mode: "nearest", intersect: false };
      options.scales.x.type = "linear";
      options.scales.x.ticks.maxTicksLimit = 6;
      options.scales.x.ticks.callback = (value) => formatClock(value);
      options.plugins.tooltip.callbacks = {
        title: (items) => (items.length ? formatClock(items[0].parsed.x) : ""),
      };
    } else {
      options.scales.y.beginAtZero = true;
      options.scales.y.ticks.precision = 0;
      options.plugins.legend.display = false;
    }
    return options;
  }

  function createChart(spec) {
    const card = document.createElement("div");
    card.className = "card";
    const title = document.createElement("h3");
    title.textContent = spec.title;
    const box = document.createElement("div");
    box.className = "chart-box";
    const canvas = document.createElement("canvas");
    box.appendChild(canvas);
    card.append(title, box);
    chartsEl.appendChild(card);

    const chart = new Chart(canvas, {
      type: spec.kind === "line" ? "line" : "bar",
      data: { datasets: [] },
      options: chartOptions(spec.kind),
    });
    applyChartTheme(chart);
    const ui = { chart, card, kind: spec.kind };
    chartUI.set(spec.key, ui);
    return ui;
  }

  function updateChart(ui, spec) {
    const chart = ui.chart;
    if (spec.kind === "line") {
      const datasets = [...spec.devices].map(([id, points]) => ({
        label: id,
        data: points,
        borderColor: colorFor(id),
        backgroundColor: colorFor(id),
        borderWidth: 2,
        pointRadius: 2,
        tension: 0,          // straight lines: curves overshoot when readings share a timestamp
      }));
      chart.data.datasets = datasets;
      chart.options.plugins.legend.display = datasets.length > 1;

      // Always show at least a 10 second window ending at the newest reading,
      // so a chart with one or two points does not get a huge time axis.
      const xs = datasets.flatMap((d) => d.data.map((p) => p.x));
      if (xs.length) {
        const newest = Math.max(...xs);
        const span = Math.max(newest - Math.min(...xs), MIN_WINDOW_MS);
        chart.options.scales.x.min = newest - span;
        chart.options.scales.x.max = newest + span * 0.02;
      }
    } else {
      const labels = [...spec.counts.keys()];
      chart.data.labels = labels;
      chart.data.datasets = [{
        label: "count",
        data: labels.map((label) => spec.counts.get(label)),
        backgroundColor: labels.map((_, i) => PALETTE[i % PALETTE.length]),
      }];
    }
    chart.update("none");
  }

  function renderCharts() {
    if (!hasChart) return;
    const specs = collectSpecs(visibleEvents());
    chartsEmptyEl.hidden = specs.length > 0;

    const wanted = new Set(specs.map((s) => s.key));
    for (const [key, ui] of chartUI) {
      if (!wanted.has(key)) {
        ui.chart.destroy();
        ui.card.remove();
        chartUI.delete(key);
      }
    }
    for (const spec of specs) {
      updateChart(chartUI.get(spec.key) || createChart(spec), spec);
    }
  }

  // ---- dark mode ----
  function currentTheme() {
    return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  }

  function setTheme(theme, save) {
    document.documentElement.dataset.theme = theme;
    if (save) {
      try { localStorage.setItem("theme", theme); } catch (err) { /* storage blocked */ }
    }
    themeToggle.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    themeToggle.setAttribute("aria-pressed", String(theme === "dark"));
    for (const ui of chartUI.values()) {
      applyChartTheme(ui.chart);
      ui.chart.update("none");
    }
  }

  themeToggle.addEventListener("click", () => {
    setTheme(currentTheme() === "dark" ? "light" : "dark", true);
  });

  // Follow the system theme live, but only until you pick one yourself.
  try {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (ev) => {
      let saved = null;
      try { saved = localStorage.getItem("theme"); } catch (err) { /* ignore */ }
      if (!saved) setTheme(ev.matches ? "dark" : "light", false);
    });
  } catch (err) { /* matchMedia not available */ }

  filterEl.addEventListener("change", () => setFilter(filterEl.value));

  // ---- connection ----
  function setStatus(live) {
    statusEl.textContent = live ? "Live" : "Disconnected: retrying...";
    statusEl.classList.toggle("live", live);
  }

  async function loadHistory() {
    try {
      const response = await fetch("/events?limit=" + HISTORY_LIMIT);
      if (!response.ok) throw new Error("HTTP " + response.status);
      ingest(await response.json());
      scheduleRender();
    } catch (err) {
      console.warn("Could not load history:", err);
    }
  }

  function connect() {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    let socket;
    try {
      socket = new WebSocket(protocol + "://" + location.host + "/ws");
    } catch (err) {
      setTimeout(connect, RECONNECT_MS);
      return;
    }
    socket.onopen = () => {
      setStatus(true);
      loadHistory();      // fill in anything missed while disconnected
    };
    socket.onclose = () => {
      setStatus(false);
      setTimeout(connect, RECONNECT_MS);
    };
    socket.onmessage = (message) => {
      try {
        const payload = JSON.parse(message.data);
        if (payload.type === "event") {
          ingest([payload.event]);
          scheduleRender();
        }
      } catch (err) {
        console.error("Bad message from server", err);
      }
    };
  }

  // ---- start ----
  if (!hasChart) {
    chartsNoticeEl.hidden = false;
    chartsNoticeEl.textContent =
      "Charts are unavailable: Chart.js could not be loaded (check your internet connection). " +
      "The device list and live feed still work.";
    chartsEmptyEl.hidden = true;
  }
  setTheme(currentTheme(), false);   // sync the button label with the theme already applied
  loadHistory();
  connect();
  setInterval(renderDevices, 1000);  // keeps the "last seen" text fresh
  scheduleRender();
})();

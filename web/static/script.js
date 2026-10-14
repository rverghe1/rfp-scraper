(() => {
  "use strict";

  const HIDDEN_COLUMNS = new Set(["Link"]);
  const TRUNCATE_COLUMNS = new Set(["Notes", "LLM Reasoning", "LLM Disqualifier"]);
  const BADGE_COLUMNS = new Set(["Bucket", "Status", "LLM Bucket", "LLM Lifecycle", "Facility Match"]);
  const FILTERABLE_COLUMNS = ["Portal", "State", "LLM Bucket", "Status"];
  const TITLE_COLUMN = "Title";
  const LINK_COLUMN = "Link";
  // Dashboard-owned, editable tracking columns - not part of the scraper's
  // sheet header until the first time anything is marked shared (see
  // web/app.py's _ensure_tracking_columns), so these always render
  // regardless of what state.header currently contains.
  const SHARED_COLUMN = "Shared in Slack";
  const SLACK_LINK_COLUMN = "Slack Thread Link";
  const FACILITY_COLUMN = "Facility";
  const VERDICT_COLUMN = "Verdict";

  const state = {
    tab: "results",
    header: [],
    rows: [],
    search: "",
    filters: {},
    sortCol: null,
    sortDir: 1,
  };

  const els = {
    tabs: document.querySelectorAll(".tab-btn"),
    rowCount: document.getElementById("rowCount"),
    refreshBtn: document.getElementById("refreshBtn"),
    searchBox: document.getElementById("searchBox"),
    filterBar: document.getElementById("filterBar"),
    statusBanner: document.getElementById("statusBanner"),
    tableHead: document.getElementById("tableHead"),
    tableBody: document.getElementById("tableBody"),
    emptyState: document.getElementById("emptyState"),
  };

  function showStatus(message, kind) {
    if (!message) {
      els.statusBanner.hidden = true;
      return;
    }
    els.statusBanner.hidden = false;
    els.statusBanner.textContent = message;
    els.statusBanner.className = "status-banner" + (kind ? " " + kind : "");
  }

  async function loadData() {
    showStatus("Loading…", "loading");
    els.refreshBtn.classList.add("spinning");
    try {
      const res = await fetch(`/api/rfps?tab=${encodeURIComponent(state.tab)}`);
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      const data = await res.json();
      state.header = data.header || [];
      state.rows = data.rows || [];
      state.filters = {};
      state.sortCol = null;
      showStatus("");
      buildFilterBar();
      render();
    } catch (err) {
      showStatus(`Couldn't load data: ${err.message}`, "error");
      state.header = [];
      state.rows = [];
      render();
    } finally {
      els.refreshBtn.classList.remove("spinning");
    }
  }

  function buildFilterBar() {
    els.filterBar.innerHTML = "";
    for (const col of FILTERABLE_COLUMNS) {
      if (!state.header.includes(col)) continue;
      const values = [...new Set(state.rows.map((r) => (r[col] || "").trim()).filter(Boolean))].sort();
      if (values.length < 2) continue;

      const select = document.createElement("select");
      select.dataset.col = col;
      const allOpt = document.createElement("option");
      allOpt.value = "";
      allOpt.textContent = `All ${col}`;
      select.appendChild(allOpt);
      for (const v of values) {
        const opt = document.createElement("option");
        opt.value = v;
        opt.textContent = v;
        select.appendChild(opt);
      }
      select.addEventListener("change", () => {
        if (select.value) state.filters[col] = select.value;
        else delete state.filters[col];
        render();
      });
      els.filterBar.appendChild(select);
    }
  }

  function matchesSearch(row) {
    if (!state.search) return true;
    const needle = state.search.toLowerCase();
    return Object.values(row).some((v) => (v || "").toLowerCase().includes(needle));
  }

  function matchesFilters(row) {
    return Object.entries(state.filters).every(([col, val]) => (row[col] || "") === val);
  }

  function parseSortValue(v) {
    if (v == null || v === "") return null;
    const t = Date.parse(v);
    if (!Number.isNaN(t) && /\d{4}/.test(v)) return t;
    const n = Number(v);
    if (!Number.isNaN(n) && v.trim() !== "") return n;
    return v.toLowerCase();
  }

  function getVisibleRows() {
    let rows = state.rows.filter((r) => matchesSearch(r) && matchesFilters(r));
    if (state.sortCol) {
      const col = state.sortCol;
      rows = rows.slice().sort((a, b) => {
        const av = parseSortValue(a[col]);
        const bv = parseSortValue(b[col]);
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        if (av < bv) return -1 * state.sortDir;
        if (av > bv) return 1 * state.sortDir;
        return 0;
      });
    }
    return rows;
  }

  function visibleColumns() {
    const cols = state.header.filter((c) => !HIDDEN_COLUMNS.has(c));
    // Always shown, even before the sheet has these columns at all (they're
    // created lazily on first save - see web/app.py).
    for (const c of [FACILITY_COLUMN, VERDICT_COLUMN, SHARED_COLUMN, SLACK_LINK_COLUMN]) {
      if (!cols.includes(c)) cols.push(c);
    }
    return cols;
  }

  async function saveTracking(row, fields) {
    const payload = {
      portal: row["Portal"] || "", title: row["Title"] || "", agency: row["Agency"] || "",
      ...fields,
    };
    const res = await fetch(`/api/rfps/slack?tab=${encodeURIComponent(state.tab)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Server returned ${res.status}`);
    }
    return res.json();
  }

  function renderHead() {
    const cols = visibleColumns();
    const tr = document.createElement("tr");
    for (const col of cols) {
      const th = document.createElement("th");
      th.textContent = col;
      if (state.sortCol === col) {
        const arrow = document.createElement("span");
        arrow.className = "sort-arrow";
        arrow.textContent = state.sortDir === 1 ? "↑" : "↓";
        th.appendChild(arrow);
      }
      th.addEventListener("click", () => {
        if (state.sortCol === col) {
          state.sortDir *= -1;
        } else {
          state.sortCol = col;
          state.sortDir = 1;
        }
        render();
      });
      tr.appendChild(th);
    }
    els.tableHead.innerHTML = "";
    els.tableHead.appendChild(tr);
  }

  function renderCell(col, row) {
    const td = document.createElement("td");
    const raw = row[col] || "";

    if (col === SHARED_COLUMN) {
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = raw.trim().toLowerCase() === "yes";
      checkbox.addEventListener("change", async () => {
        const shared = checkbox.checked;
        checkbox.disabled = true;
        try {
          const result = await saveTracking(row, { shared, link: row[SLACK_LINK_COLUMN] || "" });
          row[SHARED_COLUMN] = result.shared ? "Yes" : "";
          row[SLACK_LINK_COLUMN] = result.link || "";
          render();
        } catch (err) {
          checkbox.checked = !shared;
          showStatus(`Couldn't save: ${err.message}`, "error");
        } finally {
          checkbox.disabled = false;
        }
      });
      td.appendChild(checkbox);
      return td;
    }

    if (col === SLACK_LINK_COLUMN) {
      const wrap = document.createElement("div");
      wrap.className = "slack-link-cell";

      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = "Paste Slack thread link…";
      input.value = raw;
      input.className = "slack-link-input";

      const commit = async () => {
        const link = input.value.trim();
        if (link === raw) return; // no change
        input.disabled = true;
        try {
          // Pasting a link implies you shared it - mark shared automatically.
          const result = await saveTracking(row, { shared: link ? true : row[SHARED_COLUMN] === "Yes", link });
          row[SHARED_COLUMN] = result.shared ? "Yes" : "";
          row[SLACK_LINK_COLUMN] = result.link || "";
          render();
        } catch (err) {
          input.value = raw;
          showStatus(`Couldn't save: ${err.message}`, "error");
        } finally {
          input.disabled = false;
        }
      };
      input.addEventListener("blur", commit);
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") input.blur();
      });
      wrap.appendChild(input);

      if (raw) {
        const open = document.createElement("a");
        open.href = raw;
        open.target = "_blank";
        open.rel = "noopener noreferrer";
        open.className = "slack-link-open";
        open.textContent = "↗";
        open.title = "Open thread";
        wrap.appendChild(open);
      }
      td.appendChild(wrap);
      return td;
    }

    if (col === FACILITY_COLUMN || col === VERDICT_COLUMN) {
      const field = col === FACILITY_COLUMN ? "facility" : "verdict";
      const isVerdict = col === VERDICT_COLUMN;
      const input = document.createElement(isVerdict ? "textarea" : "input");
      if (!isVerdict) input.type = "text";
      else {
        input.rows = 2;
      }
      input.placeholder = isVerdict ? "Applying? Why or why not…" : "Facility / hospital name…";
      input.value = raw;
      input.className = "tracking-input" + (isVerdict ? " tracking-textarea" : "");

      const commit = async () => {
        const value = input.value.trim();
        if (value === raw) return;
        input.disabled = true;
        try {
          const result = await saveTracking(row, { [field]: value });
          row[col] = result[field] ?? value;
          render();
        } catch (err) {
          input.value = raw;
          showStatus(`Couldn't save: ${err.message}`, "error");
        } finally {
          input.disabled = false;
        }
      };
      input.addEventListener("blur", commit);
      if (!isVerdict) {
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") input.blur();
        });
      }
      td.appendChild(input);
      return td;
    }

    if (col === TITLE_COLUMN && row[LINK_COLUMN]) {
      const a = document.createElement("a");
      a.href = row[LINK_COLUMN];
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.className = "title-link";
      a.textContent = raw || row[LINK_COLUMN];
      td.appendChild(a);
      return td;
    }

    if (BADGE_COLUMNS.has(col) && raw) {
      const span = document.createElement("span");
      span.className = "badge";
      span.textContent = raw;
      td.appendChild(span);
      return td;
    }

    if (TRUNCATE_COLUMNS.has(col)) {
      const span = document.createElement("span");
      span.className = "cell-truncate";
      span.textContent = raw;
      span.title = raw;
      td.appendChild(span);
      return td;
    }

    td.textContent = raw;
    return td;
  }

  function renderBody(rows) {
    const cols = visibleColumns();
    els.tableBody.innerHTML = "";
    const frag = document.createDocumentFragment();
    for (const row of rows) {
      const tr = document.createElement("tr");
      for (const col of cols) tr.appendChild(renderCell(col, row));
      frag.appendChild(tr);
    }
    els.tableBody.appendChild(frag);
  }

  function render() {
    renderHead();
    const rows = getVisibleRows();
    renderBody(rows);
    els.emptyState.hidden = rows.length !== 0;
    els.rowCount.textContent = state.rows.length
      ? `${rows.length} of ${state.rows.length} row${state.rows.length === 1 ? "" : "s"}`
      : "";
  }

  els.tabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === state.tab) return;
      els.tabs.forEach((b) => b.classList.toggle("active", b === btn));
      state.tab = btn.dataset.tab;
      state.search = "";
      els.searchBox.value = "";
      loadData();
    });
  });

  els.refreshBtn.addEventListener("click", loadData);

  let searchDebounce;
  els.searchBox.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      state.search = els.searchBox.value.trim();
      render();
    }, 120);
  });

  loadData();
})();

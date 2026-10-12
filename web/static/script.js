(() => {
  "use strict";

  const HIDDEN_COLUMNS = new Set(["Link"]);
  const TRUNCATE_COLUMNS = new Set(["Notes", "LLM Reasoning", "LLM Disqualifier"]);
  const BADGE_COLUMNS = new Set(["Bucket", "Status", "LLM Bucket", "LLM Lifecycle", "Facility Match"]);
  const FILTERABLE_COLUMNS = ["Portal", "State", "LLM Bucket", "Status"];
  const TITLE_COLUMN = "Title";
  const LINK_COLUMN = "Link";

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
    return state.header.filter((c) => !HIDDEN_COLUMNS.has(c));
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

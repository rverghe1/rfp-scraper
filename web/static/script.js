(() => {
  "use strict";

  // Columns shown in the main table, in order. Everything else appears in
  // the expanded detail row (click anywhere on a row to toggle it) - same
  // pattern as the chow-scraper dashboard.
  const TABLE_COLUMNS = ["Portal", "Title", "Facility", "State", "Closing Date", "Scraped At", "LLM Bucket", "Shared in Slack"];
  const DETAIL_SECTIONS = [
    ["Posting", ["Agency", "Status", "LLM Lifecycle", "LLM Reasoning", "LLM Disqualifier", "Notes", "Link"]],
    ["Tracking", ["Slack Thread Link", "Verdict"]],
  ];
  const WIDE_FIELDS = new Set(["LLM Reasoning", "Notes", "Verdict"]);

  const BADGE_COLUMNS = new Set(["Bucket", "Status", "LLM Bucket", "LLM Lifecycle"]);
  const TRUNCATE_COLUMNS = new Set(["Notes", "LLM Reasoning", "LLM Disqualifier"]);
  const FILTERABLE_COLUMNS = ["Portal", "State", "LLM Bucket", "Status"];
  const TITLE_COLUMN = "Title";
  const LINK_COLUMN = "Link";
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
    open: new Set(),
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

  const rowKey = (r) => [r["Portal"], r["Title"], r["Agency"]].join("|");

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
      state.open.clear();
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
    const tr = document.createElement("tr");
    const chevronTh = document.createElement("th");
    chevronTh.style.width = "24px";
    tr.appendChild(chevronTh);
    for (const col of TABLE_COLUMNS) {
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

  // --- editable field widgets, shared between the summary row and the
  // expanded detail panel - every interactive element stops propagation so
  // interacting with it doesn't also toggle the row open/closed. ---

  function sharedCheckbox(row) {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = (row[SHARED_COLUMN] || "").trim().toLowerCase() === "yes";
    checkbox.addEventListener("click", (e) => e.stopPropagation());
    checkbox.addEventListener("change", async () => {
      const shared = checkbox.checked;
      checkbox.disabled = true;
      try {
        const result = await saveTracking(row, { shared, link: row[SLACK_LINK_COLUMN] || "" });
        row[SHARED_COLUMN] = result.shared ? "Yes" : "No";
        row[SLACK_LINK_COLUMN] = result.link || "";
        render();
      } catch (err) {
        checkbox.checked = !shared;
        showStatus(`Couldn't save: ${err.message}`, "error");
      } finally {
        checkbox.disabled = false;
      }
    });
    return checkbox;
  }

  function facilityField(row) {
    const raw = row[FACILITY_COLUMN] || "";
    const wrap = document.createElement("div");
    wrap.className = "link-field";
    wrap.addEventListener("click", (e) => e.stopPropagation());

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Facility / hospital name…";
    input.value = raw;
    input.className = "tracking-input";

    const commit = async () => {
      const value = input.value.trim();
      if (value === raw) return;
      input.disabled = true;
      try {
        const result = await saveTracking(row, { facility: value });
        row[FACILITY_COLUMN] = result.facility ?? value;
        delete row["Facility Link"]; // stale after a manual overwrite
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

    if (row["Facility Link"]) {
      const open = document.createElement("a");
      open.href = row["Facility Link"];
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      open.className = "link-open";
      open.textContent = "↗";
      open.title = "Open in Salesforce";
      wrap.appendChild(open);
    }
    return wrap;
  }

  function slackLinkField(row) {
    const raw = row[SLACK_LINK_COLUMN] || "";
    const wrap = document.createElement("div");
    wrap.className = "link-field";
    wrap.addEventListener("click", (e) => e.stopPropagation());

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Paste Slack thread link…";
    input.value = raw;
    input.className = "tracking-input";

    const commit = async () => {
      const link = input.value.trim();
      if (link === raw) return;
      input.disabled = true;
      try {
        const result = await saveTracking(row, { shared: link ? true : row[SHARED_COLUMN] === "Yes", link });
        row[SHARED_COLUMN] = result.shared ? "Yes" : "No";
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
      open.className = "link-open";
      open.textContent = "↗";
      open.title = "Open thread";
      wrap.appendChild(open);
    }
    return wrap;
  }

  function verdictField(row) {
    const raw = row[VERDICT_COLUMN] || "";
    const textarea = document.createElement("textarea");
    textarea.rows = 2;
    textarea.placeholder = "Applying? Why or why not…";
    textarea.value = raw;
    textarea.className = "tracking-input tracking-textarea";
    textarea.addEventListener("click", (e) => e.stopPropagation());

    const commit = async () => {
      const value = textarea.value.trim();
      if (value === raw) return;
      textarea.disabled = true;
      try {
        const result = await saveTracking(row, { verdict: value });
        row[VERDICT_COLUMN] = result.verdict ?? value;
        render();
      } catch (err) {
        textarea.value = raw;
        showStatus(`Couldn't save: ${err.message}`, "error");
      } finally {
        textarea.disabled = false;
      }
    };
    textarea.addEventListener("blur", commit);
    return textarea;
  }

  function renderCell(col, row) {
    const td = document.createElement("td");
    const raw = row[col] || "";

    if (col === SHARED_COLUMN) {
      td.appendChild(sharedCheckbox(row));
      return td;
    }

    if (col === FACILITY_COLUMN) {
      td.appendChild(facilityField(row));
      return td;
    }

    if (col === TITLE_COLUMN && row[LINK_COLUMN]) {
      const a = document.createElement("a");
      a.href = row[LINK_COLUMN];
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.className = "title-link";
      a.textContent = raw || row[LINK_COLUMN];
      a.addEventListener("click", (e) => e.stopPropagation());
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

  function detailItem(col, row) {
    const wrap = document.createElement("div");
    if (WIDE_FIELDS.has(col)) wrap.className = "wide";
    const l = document.createElement("div");
    l.className = "detail-label";
    l.textContent = col;
    const v = document.createElement("div");

    if (col === SLACK_LINK_COLUMN) {
      v.className = "detail-value";
      v.appendChild(slackLinkField(row));
    } else if (col === VERDICT_COLUMN) {
      v.className = "detail-value";
      v.appendChild(verdictField(row));
    } else {
      const raw = row[col] || "";
      v.className = "detail-value" + (raw ? "" : " empty");
      if (col === LINK_COLUMN && raw) {
        const a = document.createElement("a");
        a.href = raw;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.className = "title-link";
        a.textContent = raw;
        v.appendChild(a);
      } else {
        v.textContent = raw || "—";
      }
    }
    wrap.append(l, v);
    return wrap;
  }

  function renderDetail(row) {
    const tr = document.createElement("tr");
    tr.className = "detail-row";
    const td = document.createElement("td");
    td.colSpan = TABLE_COLUMNS.length + 1;
    const grid = document.createElement("div");
    grid.className = "detail-grid";
    for (const [title, cols] of DETAIL_SECTIONS) {
      const present = cols.filter((c) => c === SLACK_LINK_COLUMN || c === VERDICT_COLUMN || state.header.includes(c));
      if (!present.length) continue;
      const h = document.createElement("div");
      h.className = "detail-section";
      h.textContent = title;
      grid.appendChild(h);
      for (const c of present) grid.appendChild(detailItem(c, row));
    }
    td.appendChild(grid);
    tr.appendChild(td);
    return tr;
  }

  function renderBody(rows) {
    els.tableBody.innerHTML = "";
    const frag = document.createDocumentFragment();
    for (const row of rows) {
      const key = rowKey(row);
      const isOpen = state.open.has(key);
      const tr = document.createElement("tr");
      tr.className = "rfp-row" + (isOpen ? " open" : "");
      const chev = document.createElement("td");
      const c = document.createElement("span");
      c.className = "chevron";
      c.textContent = "▸";
      chev.appendChild(c);
      tr.appendChild(chev);
      for (const col of TABLE_COLUMNS) tr.appendChild(renderCell(col, row));
      tr.addEventListener("click", () => {
        if (state.open.has(key)) state.open.delete(key);
        else state.open.add(key);
        render();
      });
      frag.appendChild(tr);
      if (isOpen) frag.appendChild(renderDetail(row));
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

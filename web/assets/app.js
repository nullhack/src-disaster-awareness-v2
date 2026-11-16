(() => {
  "use strict";

  const FOLLOW_KEY = "da.follows.v1";
  const LAST_VISIT_KEY = "da.lastVisit";
  const VISIT_DISMISS_KEY = "da.visitDismissed";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  let indexPromise = null;
  let liveRegion = null;

  function siteUrl(path) {
    const clean = String(path || "").replace(/^\/+/, "");
    return new URL(clean, document.baseURI).href;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function humanDate(value) {
    const text = String(value || "").slice(0, 10);
    if (!text) return "—";
    const parsed = new Date(`${text}T00:00:00Z`);
    if (Number.isNaN(parsed.getTime())) return "—";
    return parsed.toLocaleDateString("en-GB", {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    });
  }

  function humanDateTime(value) {
    const text = String(value || "").trim();
    if (!text) return "—";
    const parsed = new Date(text);
    if (Number.isNaN(parsed.getTime())) return humanDate(text);
    return `${parsed.toLocaleString("en-GB", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
    })} UTC`;
  }

  function announce(message) {
    if (!liveRegion) {
      liveRegion = document.createElement("div");
      liveRegion.className = "visually-hidden";
      liveRegion.setAttribute("role", "status");
      liveRegion.setAttribute("aria-live", "polite");
      document.body.append(liveRegion);
    }
    liveRegion.textContent = message;
  }

  function fetchJson(path) {
    return fetch(siteUrl(path), { credentials: "same-origin" }).then((response) => {
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
    });
  }

  function loadIndex() {
    if (!indexPromise) {
      indexPromise = fetchJson("data/index.json").catch((error) => {
        indexPromise = null;
        throw error;
      });
    }
    return indexPromise;
  }

  function readFollows() {
    try {
      const value = JSON.parse(localStorage.getItem(FOLLOW_KEY) || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch {
      return {};
    }
  }

  function writeFollows(follows) {
    try {
      localStorage.setItem(FOLLOW_KEY, JSON.stringify(follows));
      return true;
    } catch {
      announce("Following could not be saved in this browser.");
      return false;
    }
  }

  let follows = readFollows();

  function followCount() {
    return Object.keys(follows).length;
  }

  function updateFollowCount() {
    const count = followCount();
    $$("[data-follow-count]").forEach((node) => {
      node.textContent = String(count);
      node.hidden = count === 0;
    });
  }

  function followButtonLabel(button, active, title) {
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.setAttribute("aria-label", `${active ? "Unfollow" : "Follow"} ${title || "this incident"}`);
    if (button.classList.contains("follow-button--icon")) {
      button.textContent = active ? "★" : "☆";
    } else {
      button.textContent = active ? "Following" : "Follow";
    }
  }

  function syncFollowButtons(root = document) {
    $$("[data-follow-id]", root).forEach((button) => {
      const id = button.dataset.followId;
      const title = button.dataset.followTitle || "";
      followButtonLabel(button, Boolean(follows[id]), title);
    });
  }

  function toggleFollow(button) {
    const id = button.dataset.followId;
    if (!id) return;
    const title = button.dataset.followTitle || "";
    const url = button.dataset.followUrl || "";
    if (follows[id]) {
      delete follows[id];
      announce(`Unfollowed ${title || "incident"}.`);
    } else {
      follows[id] = {
        id,
        title,
        url,
        added_at: new Date().toISOString(),
      };
      announce(`Following ${title || "incident"}.`);
    }
    if (writeFollows(follows)) {
      syncFollowButtons();
      updateFollowCount();
      document.dispatchEvent(new CustomEvent("follows-changed"));
    }
  }

  function incidentRowHtml(item, options = {}) {
    const title = item.display_title || item.title || "Untitled incident";
    const url = siteUrl(item.url || item.id || "");
    const type = item.incident_type || "Unknown";
    const country = item.country_name || "Global";
    const status = item.status || "active";
    const official = Number(item.official_count ?? item.coverage_stats?.official_sources ?? 0);
    const news = Number(item.news_count ?? item.coverage_stats?.news ?? 0);
    const verified = humanDate(item.last_verified_update);
    const summary = item.summary_short || "No verified summary yet. See the official record.";
    const follow = options.follow === false
      ? ""
      : `<button class="follow-button" type="button" data-follow-id="${escapeHtml(item.id)}" data-follow-title="${escapeHtml(title)}" data-follow-url="${escapeHtml(item.url || "")}" aria-pressed="false">Follow</button>`;
    return `<article class="incident-row">
      <div class="row-main">
        <p class="kicker">${escapeHtml(type)} · ${escapeHtml(country)}</p>
        <h3 class="row-title"><a href="${escapeHtml(url)}">${escapeHtml(title)}</a></h3>
        <p class="row-meta"><span class="status status--${escapeHtml(status)}">${escapeHtml(status)}</span> · ${escapeHtml(country)}</p>
        <p class="row-summary">${escapeHtml(summary)}</p>
        <p class="row-provenance">Verified ${escapeHtml(verified)} · ${official} official record${official === 1 ? "" : "s"} · ${news} article${news === 1 ? "" : "s"}</p>
      </div>
      ${follow}
    </article>`;
  }

  function renderFollowLists(index) {
    const byId = new Map((index?.incidents || []).map((item) => [item.id, item]));
    const entries = Object.values(follows);
    $$("[data-following-panel]").forEach((panel) => {
      const list = $("[data-following-list]", panel);
      if (!list) return;
      list.innerHTML = entries.map((entry) => incidentRowHtml(byId.get(entry.id) || entry)).join("");
      panel.hidden = entries.length === 0;
    });
    const page = $("[data-following-page]");
    if (page) {
      const list = $("[data-following-list]", page);
      const empty = $("[data-following-empty]", page);
      if (list) list.innerHTML = entries.map((entry) => incidentRowHtml(byId.get(entry.id) || entry)).join("");
      if (empty) empty.hidden = entries.length > 0;
    }
    syncFollowButtons();
    updateFollowCount();
  }

  function initFollowing() {
    updateFollowCount();
    syncFollowButtons();
    if (!$("[data-following-panel]") && !$("[data-following-page]")) return;
    loadIndex()
      .then(renderFollowLists)
      .catch(() => {
        /* Keep static content and stored titles if the index cannot load. */
        if ($("[data-following-page]")) {
          const empty = $("[data-following-empty]");
          if (empty) {
            empty.hidden = followCount() > 0;
            if (followCount() > 0) empty.textContent = "Following is saved, but the incident index could not be loaded.";
          }
        }
      });
    document.addEventListener("follows-changed", () => {
      loadIndex().then(renderFollowLists).catch(() => {});
    });
  }

  function dateValue(value) {
    const text = String(value || "").trim();
    if (!text) return 0;
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return Date.parse(`${text}T00:00:00Z`) || 0;
    return Date.parse(text) || 0;
  }

  function initVisitPanel() {
    const panel = $("[data-visit-panel]");
    if (!panel) return;
    const lastRaw = localStorage.getItem(LAST_VISIT_KEY);
    if (!lastRaw) {
      window.addEventListener("pagehide", () => {
        try { localStorage.setItem(LAST_VISIT_KEY, new Date().toISOString()); } catch { /* noop */ }
      });
      return;
    }
    if (localStorage.getItem(VISIT_DISMISS_KEY) === lastRaw) return;
    const last = dateValue(lastRaw);
    loadIndex().then((index) => {
      const items = index.incidents || [];
      const newItems = items.filter((item) => dateValue(item.first_seen) > last);
      const updatedOnly = items.filter((item) =>
        dateValue(item.last_verified_update) > last && dateValue(item.first_seen) <= last
      );
      const coverageOnly = items.filter((item) =>
        dateValue(item.latest_coverage_at) > last &&
        dateValue(item.last_verified_update) <= last &&
        dateValue(item.first_seen) <= last
      );
      const summary = $("[data-visit-summary]", panel);
      const list = $("[data-visit-list]", panel);
      if (!summary || !list) return;
      if (!newItems.length && !updatedOnly.length && !coverageOnly.length) {
        summary.textContent = dateValue(index.as_of) <= last
          ? `No new data since ${humanDate(lastRaw)}.`
          : `No verified changes since ${humanDate(lastRaw)}.`;
        list.innerHTML = "";
      } else {
        const parts = [];
        if (newItems.length) parts.push(`${newItems.length} new`);
        if (updatedOnly.length) parts.push(`${updatedOnly.length} updated`);
        if (coverageOnly.length) parts.push(`${coverageOnly.length} with new coverage`);
        summary.textContent = `${parts.join(" · ")} since ${humanDate(lastRaw)}. Updated means a verified update, not just a new article.`;
        const rows = [...newItems, ...updatedOnly, ...coverageOnly].slice(0, 8);
        list.innerHTML = rows.map((item) => {
          const reason = newItems.includes(item) ? "New" : updatedOnly.includes(item) ? "Updated" : "New coverage";
          return `<p><strong>${escapeHtml(reason)}</strong> · <a href="${escapeHtml(siteUrl(item.url))}">${escapeHtml(item.display_title)}</a> <span class="meta">${escapeHtml(humanDate(item.last_verified_update))}</span></p>`;
        }).join("");
      }
      panel.hidden = false;
    }).catch(() => {});
    const dismiss = $("[data-dismiss-visit]", panel);
    if (dismiss) {
      dismiss.addEventListener("click", () => {
        panel.hidden = true;
        try { localStorage.setItem(VISIT_DISMISS_KEY, lastRaw); } catch { /* noop */ }
      });
    }
    window.addEventListener("pagehide", () => {
      try { localStorage.setItem(LAST_VISIT_KEY, new Date().toISOString()); } catch { /* noop */ }
    });
  }

  function initDataAsOf() {
    const node = $("[data-data-as-of]");
    if (!node) return;
    loadIndex().then((index) => {
      node.textContent = humanDateTime(index.generated_at || index.as_of);
    }).catch(() => {
      node.textContent = "unavailable";
    });
  }

  function initIncidentFilters() {
    const form = $("[data-incident-filters]");
    const list = $("[data-incident-list]");
    if (!form || !list) return;
    const rows = $$(".incident-row", list);
    const search = $("#incident-search", form);
    const status = $("#filter-status", form);
    const region = $("#filter-region", form);
    const type = $("#filter-type", form);
    const sort = $("#filter-sort", form);
    const count = $("[data-result-count]");
    const params = new URLSearchParams(window.location.search);
    const hadStatus = params.has("status");

    if (params.get("q")) search.value = params.get("q");
    status.value = hadStatus ? (params.get("status") || "") : "active";
    if (params.get("region")) region.value = params.get("region");
    if (params.get("type")) type.value = params.get("type");
    if (params.get("sort")) sort.value = params.get("sort");

    let empty = null;

    function compare(a, b) {
      const mode = sort.value;
      if (mode === "title") {
        return a.dataset.title.localeCompare(b.dataset.title);
      }
      if (mode === "official") {
        return Number(b.dataset.official || 0) - Number(a.dataset.official || 0) ||
          String(b.dataset.verified || "").localeCompare(String(a.dataset.verified || ""));
      }
      const key = mode === "event" ? "event" : mode === "first" ? "first" : "verified";
      return String(b.dataset[key] || "").localeCompare(String(a.dataset[key] || ""));
    }

    function apply(writeUrl = true) {
      const query = search.value.trim().toLowerCase();
      const wantedStatus = status.value;
      const wantedRegion = region.value;
      const wantedType = type.value;
      let visible = 0;
      const ordered = [...rows].sort(compare);
      ordered.forEach((row) => {
        const matchesQuery = !query || row.textContent.toLowerCase().includes(query) ||
          String(row.dataset.title || "").includes(query);
        const matchesStatus = !wantedStatus || row.dataset.status === wantedStatus;
        const matchesRegion = !wantedRegion || row.dataset.region === wantedRegion;
        const matchesType = !wantedType || row.dataset.type === wantedType;
        const show = matchesQuery && matchesStatus && matchesRegion && matchesType;
        row.hidden = !show;
        if (show) visible += 1;
        list.append(row);
      });
      if (count) count.textContent = `${visible} incident${visible === 1 ? "" : "s"}`;
      if (!empty) {
        empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent = "No incidents match this view. Try clearing a filter or searching all statuses.";
        empty.hidden = true;
        list.after(empty);
      }
      empty.hidden = visible !== 0;
      if (writeUrl) {
        const next = new URLSearchParams();
        if (query) next.set("q", search.value.trim());
        if (wantedStatus !== "active") next.set("status", wantedStatus);
        if (wantedRegion) next.set("region", wantedRegion);
        if (wantedType) next.set("type", wantedType);
        if (sort.value !== "verified") next.set("sort", sort.value);
        const suffix = next.toString() ? `?${next}` : "";
        history.replaceState(null, "", `${window.location.pathname}${suffix}${window.location.hash}`);
      }
    }

    let timer = 0;
    search.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => apply(), 150);
    });
    [status, region, type, sort].forEach((control) => control.addEventListener("change", () => apply()));
    form.addEventListener("submit", (event) => event.preventDefault());
    const clear = $("[data-clear-filters]", form);
    if (clear) {
      clear.addEventListener("click", () => {
        search.value = "";
        status.value = "active";
        region.value = "";
        type.value = "";
        sort.value = "verified";
        apply();
      });
    }
    window.addEventListener("popstate", () => {
      const current = new URLSearchParams(window.location.search);
      search.value = current.get("q") || "";
      status.value = current.has("status") ? (current.get("status") || "") : "active";
      region.value = current.get("region") || "";
      type.value = current.get("type") || "";
      sort.value = current.get("sort") || "verified";
      apply(false);
    });
    apply(false);
  }

  function updateItemHtml(update) {
    const count = Array.isArray(update.news_ids) ? update.news_ids.length : 0;
    const meta = count ? `<p class="meta">${count} linked article${count === 1 ? "" : "s"}</p>` : "";
    return `<article class="update" id="u-${escapeHtml(update.log_date)}">
      <h3><time datetime="${escapeHtml(update.log_date)}">${escapeHtml(humanDate(update.log_date))}</time></h3>
      <p>${escapeHtml(update.summary || "No summary text was generated for this update.")}</p>
      ${meta}
    </article>`;
  }

  function initUpdateLoadMore() {
    $$("[data-load-updates]").forEach((button) => {
      button.addEventListener("click", () => {
        const id = button.dataset.incidentId;
        const target = button.nextElementSibling;
        if (!id || !target) return;
        button.disabled = true;
        fetchJson(`data/incidents/${id}.json`).then((detail) => {
          const updates = Array.isArray(detail.updates) ? detail.updates : [];
          const loaded = Number(button.dataset.loaded || 0);
          const start = 10 + loaded;
          const batch = updates.slice(start, start + 10);
          target.insertAdjacentHTML("beforeend", batch.map(updateItemHtml).join(""));
          const nextLoaded = loaded + batch.length;
          button.dataset.loaded = String(nextLoaded);
          button.disabled = false;
          if (start + batch.length >= updates.length) {
            button.remove();
            announce("All verified updates are shown.");
          } else {
            const remaining = updates.length - (start + batch.length);
            button.textContent = `Show ${Math.min(10, remaining)} more updates`;
            button.setAttribute("aria-expanded", "true");
          }
        }).catch(() => {
          button.disabled = false;
          button.textContent = "Updates could not load — try again";
          announce("Verified updates could not load.");
        });
      });
    });
  }

  function initCopyLink() {
    $$("[data-copy-link]").forEach((button) => {
      button.addEventListener("click", () => {
        const done = () => {
          const original = button.textContent;
          button.textContent = "Copied";
          announce("Link copied to clipboard.");
          window.setTimeout(() => { button.textContent = original; }, 1800);
        };
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(window.location.href).then(done).catch(() => announce("Copy failed."));
        } else {
          announce("Copy is not available in this browser.");
        }
      });
    });
  }

  function initCoverage() {
    const root = $("[data-coverage-json]");
    if (!root) return;
    const incidentId = root.dataset.coverageJson;
    if (!incidentId) return;
    const list = $("[data-coverage-list]");
    const count = $("[data-coverage-count]");
    const search = $("[data-coverage-search]");
    const tabs = $$("[data-coverage-tab]");
    const panels = $$("[data-coverage-panel]");
    const pagination = $("[data-coverage-pagination]");
    if (!list) return;

    let coverage = null;
    let articleById = new Map();
    let state = { view: "clusters", q: "", page: 1 };
    const pageSize = 25;

    function readState() {
      const params = new URLSearchParams(window.location.search);
      state.view = params.get("view") || "clusters";
      state.q = params.get("q") || "";
      state.page = Math.max(1, Number(params.get("page") || 1));
    }

    function writeState() {
      const params = new URLSearchParams();
      if (state.view !== "clusters") params.set("view", state.view);
      if (state.q) params.set("q", state.q);
      if (state.page > 1) params.set("page", String(state.page));
      const suffix = params.toString() ? `?${params}` : "";
      history.replaceState(null, "", `${window.location.pathname}${suffix}`);
    }

    function setView(view) {
      state.view = view;
      tabs.forEach((tab) => {
        const selected = tab.dataset.coverageTab === view;
        tab.setAttribute("aria-selected", selected ? "true" : "false");
        tab.tabIndex = selected ? 0 : -1;
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.coveragePanel !== view;
      });
      writeState();
    }

    function populateArticles(container, ids) {
      if (container.dataset.loaded === "1") return;
      const items = ids.map((id) => articleById.get(id)).filter(Boolean);
      if (!items.length) {
        container.textContent = "Article links are not available in this build.";
        container.dataset.loaded = "1";
        return;
      }
      container.innerHTML = `<ul>${items.map((article) =>
        `<li><a href="${escapeHtml(article.url)}" rel="noopener noreferrer">${escapeHtml(article.headline)}</a> <span class="meta">${escapeHtml(article.outlet || article.domain || "News")}${article.published_date ? ` · ${escapeHtml(humanDate(article.published_date))}` : ""}</span></li>`
      ).join("")}</ul>`;
      container.dataset.loaded = "1";
    }

    function clusterNode(cluster) {
      const li = document.createElement("li");
      li.className = "coverage-cluster";
      const outlets = Array.isArray(cluster.outlets) ? cluster.outlets : [];
      const outletText = cluster.outlet_count > 1 ? `${cluster.outlet_count} outlets` : (outlets[0] || "News");
      const dates = Array.isArray(cluster.date_range) ? cluster.date_range : [];
      const dateText = dates.length ? humanDate(dates[0]) : "Undated";
      li.innerHTML = `<div class="cluster-main">
        <a class="cluster-title" href="${escapeHtml(cluster.url)}" rel="noopener noreferrer">${escapeHtml(cluster.title)}</a>
        <p class="meta">${escapeHtml(cluster.outlet || "News")} · ${escapeHtml(outletText)} · ${escapeHtml(dateText)}</p>
      </div>`;
      if (cluster.count > 1) {
        const details = document.createElement("details");
        details.className = "cluster-details";
        details.innerHTML = `<summary>Show ${Number(cluster.count)} article link${Number(cluster.count) === 1 ? "" : "s"}</summary><div data-cluster-articles></div>`;
        const holder = $("[data-cluster-articles]", details);
        details.addEventListener("toggle", () => {
          if (details.open && holder) populateArticles(holder, cluster.article_ids || []);
        });
        li.append(details);
      }
      return li;
    }

    function filteredClusters() {
      const query = state.q.trim().toLowerCase();
      const clusters = Array.isArray(coverage.clusters) ? coverage.clusters : [];
      if (!query) return clusters;
      return clusters.filter((cluster) => {
        const haystack = [cluster.title, cluster.outlet, ...(cluster.outlets || [])].join(" ").toLowerCase();
        return haystack.includes(query);
      });
    }

    function render() {
      if (!coverage) return;
      const clusters = filteredClusters();
      const pages = Math.max(1, Math.ceil(clusters.length / pageSize));
      state.page = Math.min(Math.max(1, state.page), pages);
      const start = (state.page - 1) * pageSize;
      list.innerHTML = "";
      const pageItems = clusters.slice(start, start + pageSize);
      if (!pageItems.length) {
        list.innerHTML = '<p class="empty">No coverage matches this search. Clear the search to see the full trail.</p>';
      } else {
        const ul = document.createElement("ul");
        ul.className = "coverage-list";
        pageItems.forEach((cluster) => ul.append(clusterNode(cluster)));
        list.append(ul);
      }
      if (count) {
        const total = coverage.stats?.news || coverage.articles?.length || 0;
        count.textContent = `${clusters.length} story cluster${clusters.length === 1 ? "" : "s"}${state.q ? " match" : " shown"} · ${total} articles in total`;
      }
      if (pagination) {
        pagination.innerHTML = "";
        if (pages > 1) {
          const makeButton = (label, page, current = false, disabled = false) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "button button--ghost button--small";
            button.textContent = label;
            if (current) button.setAttribute("aria-current", "page");
            button.disabled = disabled;
            button.addEventListener("click", () => {
              state.page = page;
              render();
              writeState();
              list.scrollIntoView({ block: "start", behavior: "smooth" });
            });
            return button;
          };
          pagination.append(makeButton("Previous", state.page - 1, false, state.page === 1));
          pagination.append(makeButton(`Page ${state.page} of ${pages}`, state.page, true));
          pagination.append(makeButton("Next", state.page + 1, false, state.page === pages));
        }
      }
    }

    readState();
    setView(["clusters", "official", "updates"].includes(state.view) ? state.view : "clusters");
    if (search) search.value = state.q;
    fetchJson(`data/coverage/${incidentId}.json`).then((data) => {
      coverage = data;
      articleById = new Map((data.articles || []).map((article) => [article.news_id, article]));
      render();
    }).catch(() => {
      if (count) count.textContent = "The full coverage index could not be loaded. The first page of links is still shown.";
    });

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => setView(tab.dataset.coverageTab));
    });
    let timer = 0;
    if (search) {
      search.addEventListener("input", () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => {
          state.q = search.value.trim();
          state.page = 1;
          if (coverage) render();
          writeState();
        }, 150);
      });
    }
    window.addEventListener("popstate", () => {
      readState();
      if (search) search.value = state.q;
      setView(["clusters", "official", "updates"].includes(state.view) ? state.view : "clusters");
      if (coverage) render();
    });
  }

  function initNewsSubmit() {
    const form = $("[data-news-submit]");
    if (!form) return;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const urlInput = $("#submit-url", form);
      const headlineInput = $("#submit-headline", form);
      const url = urlInput.value.trim();
      let parsed;
      try {
        parsed = new URL(url);
      } catch {
        urlInput.setCustomValidity("Enter a complete article URL, including https://.");
        urlInput.reportValidity();
        return;
      }
      if (!["http:", "https:"].includes(parsed.protocol)) {
        urlInput.setCustomValidity("Only http and https links are accepted.");
        urlInput.reportValidity();
        return;
      }
      urlInput.setCustomValidity("");
      const repo = form.dataset.repo;
      const headline = headlineInput.value.trim();
      const title = headline ? `News submission: ${headline}` : "News submission";
      const body = `### Article URL\n\n${url}\n\n### Headline (optional)\n\n${headline || "_No response_"}\n\nSubmitted from the public site. A maintainer reviews anything the classifier is unsure about.`;
      const target = new URL(`https://github.com/${repo}/issues/new`);
      target.searchParams.set("template", "news_submission.yml");
      target.searchParams.set("title", title);
      target.searchParams.set("body", body);
      window.location.assign(target.toString());
    });
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-follow-id]");
    if (button) {
      event.preventDefault();
      toggleFollow(button);
    }
  });

  initFollowing();
  initVisitPanel();
  initDataAsOf();
  initIncidentFilters();
  initUpdateLoadMore();
  initCopyLink();
  initCoverage();
  initNewsSubmit();
})();

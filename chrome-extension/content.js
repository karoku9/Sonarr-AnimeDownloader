(() => {
  if (document.getElementById("anidown-v3-panel")) return;

  const state = {
    serverUrl: "http://localhost:5000",
    collapsed: false,
    showMapped: false,
    page: null,
    candidates: [],
    selected: null,
    selectedSeason: "",
    audio: "UNKNOWN",
    mode: "add_url",
    clearReview: true,
    lastRequest: "None",
    lastResponse: "None"
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function pageTitle() {
    const visible = document.querySelector("h1, [itemprop='name'], .anime-title, .title");
    return ((visible && visible.textContent) || document.title || "").replace(/\s*[-|]\s*AnimeWorld.*$/i, "").trim();
  }

  function audioFor(title, url) {
    return /(?:\(|[-_. ])ita(?:\)|[-_. /]|$)/i.test(`${title} ${url}`) ? "DUB" : (url.includes("/play/") ? "SUB" : "UNKNOWN");
  }

  function pageUrl() {
    const canonical = document.querySelector("link[rel='canonical']");
    return canonical && /animeworld\./i.test(canonical.href) ? canonical.href : location.href;
  }

  const panel = el("aside", "anidown-panel");
  panel.id = "anidown-v3-panel";
  panel.innerHTML = `
    <header><strong>AniDown v3</strong><button class="collapse" title="Collapse panel">&lsaquo;</button></header>
    <main>
      <section class="current"></section>
      <label class="toggle"><input class="show-mapped" type="checkbox"> Show already mapped</label>
      <div class="connection">Connecting...</div>
      <section class="mapping"></section>
      <section class="candidates"></section>
      <details class="debug"><summary>Debug info</summary><div class="debug-content"></div><button class="copy-debug">Copy debug info</button></details>
    </main>
    <footer class="savebar"><button class="save" disabled>Save mapping</button></footer>`;
  document.documentElement.classList.add("anidown-panel-page");
  document.body.appendChild(panel);

  const current = panel.querySelector(".current");
  const connection = panel.querySelector(".connection");
  const candidateList = panel.querySelector(".candidates");
  const mapping = panel.querySelector(".mapping");
  const savebar = panel.querySelector(".savebar");
  const saveButton = panel.querySelector(".save");
  const debugContent = panel.querySelector(".debug-content");

  function setMessage(message, tone = "") {
    connection.textContent = message;
    connection.className = `connection ${tone}`;
  }

  function renderDebug() {
    debugContent.textContent = [
      `Server: ${state.serverUrl}`,
      `Connection: ${connection.textContent}`,
      `Last request: ${state.lastRequest}`,
      `Last response: ${state.lastResponse}`
    ].join("\n");
  }

  function renderCurrent() {
    current.innerHTML = "";
    current.append(el("h2", "", state.page.title || "AnimeWorld page"));
    current.append(el("p", "url", state.page.url));
    current.append(el("span", `audio ${state.audio.toLowerCase()}`, `Audio: ${state.audio}`));
  }

  function statusBadge(status) {
    return el("span", `status ${status}`, String(status || "unknown").replaceAll("_", " "));
  }

  function selectedSeason() {
    return state.selected && state.selected.seasons.find(season => String(season.season) === String(state.selectedSeason));
  }

  function chooseSeason(season) {
    state.selectedSeason = String(season);
    const currentSeason = selectedSeason();
    state.mode = currentSeason && currentSeason.mapped ? "replace_season" : "add_url";
    renderMapping();
  }

  function choose(candidate) {
    state.selected = candidate;
    const firstEmpty = candidate.seasons.find(season => !season.mapped);
    const first = firstEmpty || candidate.seasons[0] || {season: "1", mapped: false};
    state.selectedSeason = String(first.season);
    state.mode = first.mapped ? "replace_season" : "add_url";
    state.clearReview = true;
    renderCandidates();
    renderMapping();
  }

  function renderCandidates() {
    candidateList.innerHTML = "";
    candidateList.append(el("h3", "", "Other candidates"));
    if (!state.candidates.length) {
      candidateList.append(el("p", "muted", "No review or unmapped candidates found."));
      return;
    }
    state.candidates.slice(0, 20).forEach(candidate => {
      const button = el("button", "candidate" + (state.selected === candidate ? " selected" : ""));
      button.append(el("strong", "", candidate.title), statusBadge(candidate.status), el("div", "candidate-meta", `${Number(candidate.score).toFixed(2)} | ${candidate.reason}`));
      button.addEventListener("click", () => choose(candidate));
      candidateList.append(button);
    });
  }

  function labeled(label, control) {
    const wrapper = el("label", "control", label);
    wrapper.append(control);
    return wrapper;
  }

  function renderMapping() {
    mapping.innerHTML = "";
    savebar.classList.toggle("visible", !!state.selected);
    saveButton.disabled = !state.selected || !state.selectedSeason;
    if (!state.selected) {
      mapping.append(el("p", "muted editor-empty", "Select a candidate to edit its mapping."));
      return;
    }
    mapping.append(el("h3", "", "Selected mapping"));
    mapping.append(el("strong", "selected-title", state.selected.title));
    mapping.append(el("p", "page-summary", `Page: ${state.page.title}`), el("p", "url", state.page.url));
    if (state.selected.multiple_possible_candidates) mapping.append(el("p", "warning", "Multiple possible candidates. Please confirm."));
    const seasonList = el("div", "season-options");
    state.selected.seasons.forEach(season => {
      const row = el("button", "season-option" + (String(season.season) === state.selectedSeason ? " selected" : ""));
      row.type = "button";
      row.append(el("strong", "", `Season ${season.season}`), statusBadge(season.status || (season.mapped ? "mapped" : "empty")), el("span", `audio ${String(season.audio || "UNKNOWN").toLowerCase()}`, season.audio || "UNKNOWN"));
      row.append(el("p", "existing-url", season.urls.length ? season.urls.join("\n") : "No existing URL"));
      row.append(el("small", "", "Use current page for this season"));
      row.addEventListener("click", () => chooseSeason(season.season));
      seasonList.append(row);
    });
    mapping.append(seasonList);
    const addRow = el("div", "add-season");
    const addInput = el("input", "new-season");
    addInput.type = "number"; addInput.min = "1"; addInput.placeholder = "New season";
    const addButton = el("button", "secondary", "Add season");
    addButton.type = "button";
    addButton.addEventListener("click", () => {
      const number = String(addInput.value || "").trim();
      if (!/^\d+$/.test(number) || Number(number) < 1) return setMessage("Select a valid season first.", "error");
      if (!state.selected.seasons.some(season => String(season.season) === number)) state.selected.seasons.push({season: number, mapped: false, urls: [], audio: "UNKNOWN", status: "empty", needs_review: false});
      chooseSeason(number);
    });
    addRow.append(addInput, addButton);
    mapping.append(addRow);
    const audioSelect = el("select", "audio-select");
    ["SUB", "DUB", "UNKNOWN"].forEach(audio => {
      const option = el("option", "", audio); option.value = audio; option.selected = audio === state.audio; audioSelect.append(option);
    });
    audioSelect.addEventListener("change", () => { state.audio = audioSelect.value; renderCurrent(); });
    const modeSelect = el("select", "mode-select");
    [["add_url", "Add URL"], ["replace_season", "Replace season URL"], ["replace_all_season_urls", "Replace all URLs for season"]].forEach(([value, label]) => {
      const option = el("option", "", label); option.value = value; option.selected = value === state.mode; modeSelect.append(option);
    });
    modeSelect.addEventListener("change", () => state.mode = modeSelect.value);
    mapping.append(labeled("Detected audio for this mapping", audioSelect), labeled("Save mode", modeSelect));
    const clearLabel = el("label", "toggle clear-review");
    const clearInput = el("input"); clearInput.type = "checkbox"; clearInput.checked = state.clearReview;
    clearInput.addEventListener("change", () => state.clearReview = clearInput.checked);
    clearLabel.append(clearInput, document.createTextNode(" Clear needs review after save"));
    mapping.append(clearLabel);
    const existing = selectedSeason();
    if (existing && existing.mapped) mapping.append(el("p", "warning", "This season is already mapped. Replace existing URL?"));
  }

  async function request(path, body) {
    state.lastRequest = `${body ? "POST" : "GET"} ${path}${body ? " " + JSON.stringify(body) : ""}`;
    renderDebug();
    try {
      const response = await fetch(`${state.serverUrl}${path}`, {
        method: body ? "POST" : "GET",
        headers: body ? {"Content-Type": "application/json"} : {},
        body: body ? JSON.stringify(body) : undefined
      });
      const payload = await response.json();
      state.lastResponse = JSON.stringify(payload).slice(0, 600);
      renderDebug();
      if (!response.ok || payload.error) throw new Error(payload.error || "Backend returned an error.");
      return payload.data;
    } catch (error) {
      state.lastResponse = error.message.includes("fetch") ? "CORS or network failure." : error.message;
      renderDebug();
      throw error;
    }
  }

  async function loadCandidates() {
    setMessage("Connecting to AniDown...");
    try {
      const status = await request("/api/extension/status");
      setMessage(`${status.version} connected | default ${status.global_language_preference}`, "ok");
      const data = await request("/api/extension/current-page-candidates", {
        page_title: document.title,
        detected_title: state.page.title,
        url: state.page.url,
        detected_audio: state.audio,
        show_already_mapped: state.showMapped
      });
      state.candidates = data.candidates || [];
      const best = state.candidates.find(candidate => candidate.preselected);
      if (best) choose(best);
      else {
        state.selected = null;
        renderCandidates();
        renderMapping();
        if (state.candidates[0] && state.candidates[0].multiple_possible_candidates) setMessage("Multiple possible candidates. Please confirm.", "warning");
      }
    } catch (error) {
      setMessage(error.message.includes("fetch") ? "AniDown server unreachable or CORS error: check server URL." : `AniDown unavailable: ${error.message}`, "error");
      candidateList.innerHTML = "";
      renderMapping();
    }
  }

  async function saveMapping(confirmed) {
    if (!state.selected) return setMessage("Selected candidate missing.", "error");
    if (!state.selectedSeason) return setMessage("Select a season first.", "error");
    try {
      const result = await request("/api/extension/save-mapping", {
        series_title: state.selected.title,
        season: Number(state.selectedSeason),
        url: state.page.url,
        audio: state.audio,
        mode: state.mode,
        clear_needs_review: state.clearReview,
        confirmed
      });
      if (result.action === "needs_confirmation") {
        setMessage("This season is already mapped. Confirmation required.", "warning");
        if (window.confirm("This season is already mapped. Replace existing URL?")) await saveMapping(true);
        return;
      }
      setMessage(result.message, "ok");
      await loadCandidates();
    } catch (error) {
      setMessage(`Save failed: ${error.message}`, "error");
    }
  }

  saveButton.addEventListener("click", () => saveMapping(false));
  panel.querySelector(".copy-debug").addEventListener("click", () => navigator.clipboard.writeText(debugContent.textContent).then(() => setMessage("Debug info copied.", "ok")).catch(() => setMessage("Unable to copy debug info.", "error")));
  panel.querySelector(".collapse").addEventListener("click", () => {
    state.collapsed = !state.collapsed;
    panel.classList.toggle("collapsed", state.collapsed);
    document.documentElement.classList.toggle("anidown-panel-collapsed", state.collapsed);
    panel.querySelector(".collapse").textContent = state.collapsed ? ">" : "<";
    chrome.storage.sync.set({panelCollapsed: state.collapsed});
  });
  panel.querySelector(".show-mapped").addEventListener("change", event => {
    state.showMapped = event.target.checked;
    loadCandidates();
  });

  chrome.storage.sync.get({serverUrl: "http://localhost:5000", panelCollapsed: false}, settings => {
    state.serverUrl = settings.serverUrl.replace(/\/+$/, "");
    state.collapsed = !!settings.panelCollapsed;
    panel.classList.toggle("collapsed", state.collapsed);
    document.documentElement.classList.toggle("anidown-panel-collapsed", state.collapsed);
    panel.querySelector(".collapse").textContent = state.collapsed ? ">" : "<";
    state.page = {title: pageTitle(), url: pageUrl()};
    state.audio = audioFor(state.page.title, state.page.url);
    renderCurrent();
    renderDebug();
    loadCandidates();
  });
})();

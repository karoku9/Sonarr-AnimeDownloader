'use strict';

const h = React.createElement;
const LANGUAGES = ["", "AUTO", "DUB_FIRST", "SUB_FIRST", "DUB_ONLY", "SUB_ONLY"];
const TABS = [
  ["dashboard", "dashboard", "Dashboard"],
  ["mapping", "account_tree", "Mapping Manager"],
  ["review", "fact_check", "Needs Review"],
  ["parser", "manage_search", "Sonarr Parser"],
  ["report", "assessment", "Search-only Report"],
  ["settings", "settings", "Settings"],
  ["logs", "receipt_long", "Logs"]
];

function apiJson(url, options = {}) {
  return fetch(url, Object.assign({headers: {"Content-Type": "application/json"}}, options)).then(async response => {
    const payload = await response.json();
    if (!response.ok || payload.error) throw new Error(typeof payload.error == "string" ? payload.error : "Request failed");
    return payload.search_engine == "v3" ? payload : payload.data;
  });
}

function showMessage(message) {
  if (typeof showToast == "function") showToast(message);
  else console.log(message);
}

function normalize(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function validUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol == "http:" || url.protocol == "https:";
  } catch (_) {
    return false;
  }
}

function normalizeUrl(value) {
  try {
    const url = new URL(String(value || "").trim());
    url.hash = "";
    url.pathname = url.pathname.replace(/\/+$/, "") || "/";
    return url.toString().replace(/\/$/, "");
  } catch (_) {
    return String(value || "").trim().replace(/\/+$/, "");
  }
}

function sourceSlug(url) {
  try {
    const path = new URL(String(url || "")).pathname.replace(/\/+$/, "");
    return (path.split("/").pop() || "").split(".")[0].toLowerCase();
  } catch (_) {
    return String(url || "").split("/").pop().split(".")[0].toLowerCase();
  }
}

function urlPartNumber(url) {
  const slug = sourceSlug(url);
  const patterns = [
    /(?:^|[-_ .])part[-_ .]*(\d+)(?:$|[-_ .])/i,
    /(?:^|[-_ .])parte[-_ .]*(\d+)(?:$|[-_ .])/i,
    /(?:^|[-_ .])cour[-_ .]*(\d+)(?:$|[-_ .])/i,
    /(?:^|[-_ .])(\d+)(?:st|nd|rd|th)[-_ .]*cour(?:$|[-_ .])/i,
    /(?:^|[-_ .])stagione[-_ .]*\d+[-_ .]*parte[-_ .]*(\d+)(?:$|[-_ .])/i,
    /(?:^|[-_ .])season[-_ .]*\d+[-_ .]*part[-_ .]*(\d+)(?:$|[-_ .])/i
  ];
  for (const pattern of patterns) {
    const match = slug.match(pattern);
    if (match) return Math.max(2, Number(match[1]) || 2);
  }
  return slug ? 1 : null;
}

function sortPartUrls(urls) {
  return urls.map((url, index) => ({url, index, part: urlPartNumber(url)})).sort((a, b) => {
    if (a.part == null && b.part == null) return a.index - b.index;
    if (a.part == null) return 1;
    if (b.part == null) return -1;
    return a.part == b.part ? normalizeUrl(a.url).localeCompare(normalizeUrl(b.url)) : a.part - b.part;
  }).map(item => item.url);
}

function urlOrderWarning(urls) {
  const parts = urls.filter(Boolean).map(urlPartNumber).filter(value => value != null);
  if (urls.some(url => String(url || "").toLowerCase().includes("/tba"))) return true;
  return parts.length > 1 && (parts.join(",") != parts.slice().sort((a, b) => a - b).join(",") || (!parts.includes(1) && parts.some(value => value > 1)));
}

function seasonSortValue(value) {
  const text = String(value || "");
  return /^\d+$/.test(text) ? [0, Number(text)] : text == "absolute" ? [1, 0] : [2, text];
}

function sortSeasons(seasons) {
  return seasons.slice().sort((left, right) => {
    const a = seasonSortValue(left.season), b = seasonSortValue(right.season);
    return a[0] == b[0] ? (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0) : a[0] - b[0];
  });
}

function blankSeries() {
  return {
    id: "new-" + Date.now(),
    title: "",
    absolute: false,
    seasons: [{season: "1", urls: [""]}],
    language_preference: "",
    effective_language_preference: "AUTO",
    notes: "",
    needs_review: true,
    allow_shared_url_across_seasons: false,
    aliases: []
  };
}

function rowsToSeries(rows, aliases = {}) {
  const grouped = {};
  (rows || []).forEach((row, index) => {
    const id = row._mapping_id || row._entry_id || row.sonarr_title || ("row-" + index);
    if (!grouped[id]) grouped[id] = {
      id, title: row.sonarr_title || "", absolute: !!row.absolute, seasons: [],
      language_preference: row.language_preference || "",
      effective_language_preference: row.effective_language_preference || "AUTO",
      notes: row.notes || "", needs_review: !!row.needs_review,
      allow_shared_url_across_seasons: !!row.allow_shared_url_across_seasons,
      aliases: aliases[row.sonarr_title] || []
    };
    let season = grouped[id].seasons.find(item => item.season == row.season);
    if (!season) {
      season = {season: row.season || (row.absolute ? "absolute" : "1"), urls: []};
      grouped[id].seasons.push(season);
    }
    season.urls.push(row.source_url || "");
  });
  return Object.values(grouped).map(serie => Object.assign({}, serie, {seasons: sortSeasons(serie.seasons)}));
}

function seriesToRows(seriesList) {
  const rows = [];
  seriesList.forEach((serie, entryIndex) => {
    const seasons = serie.seasons.length ? sortSeasons(serie.seasons) : [{season: serie.absolute ? "absolute" : "", urls: [""]}];
    seasons.forEach(season => (season.urls.length ? season.urls : [""]).forEach(url => rows.push({
      _entry_id: String(entryIndex), sonarr_title: serie.title,
      _mapping_id: serie.id,
      season: serie.absolute ? "absolute" : season.season, source_url: url,
      absolute: !!serie.absolute, language_preference: serie.language_preference || "",
      effective_language_preference: serie.effective_language_preference || "AUTO",
      notes: serie.notes || "", needs_review: !!serie.needs_review || !url,
      allow_shared_url_across_seasons: !!serie.allow_shared_url_across_seasons
    })));
  });
  return rows;
}

function validateSeries(seriesList) {
  const rows = seriesToRows(seriesList), issues = [], titles = {};
  rows.forEach((row, index) => {
    const title = String(row.sonarr_title || "").trim();
    const season = String(row.season || "").trim();
    const url = String(row.source_url || "").trim();
    if (!title) issues.push({row: index, series: row._entry_id, level: "error", message: "Title cannot be empty."});
    if (title) {
      titles[normalize(title)] = titles[normalize(title)] || new Set();
      titles[normalize(title)].add(row._entry_id || String(index));
    }
    if ((row.absolute && season != "absolute") || (!row.absolute && !/^\d+$/.test(season))) issues.push({series: row._entry_id, level: "error", message: "Invalid season key."});
    if (!url) issues.push({series: row._entry_id, level: "warning", message: "Incomplete mapping: URL is empty."});
    else if (!validUrl(url)) issues.push({series: row._entry_id, level: "error", message: "Invalid URL."});
    if (row.language_preference && !LANGUAGES.includes(row.language_preference)) issues.push({series: row._entry_id, level: "error", message: "Invalid language preference."});
  });
  rows.forEach(row => {
    if (row.sonarr_title && titles[normalize(row.sonarr_title)].size > 1) issues.push({series: row._entry_id, level: "warning", message: "Duplicate title."});
  });
  seriesList.forEach(serie => {
    const groups = {};
    sortSeasons(serie.seasons).forEach(season => season.urls.filter(Boolean).forEach(url => {
      const key = normalizeUrl(url);
      groups[key] = groups[key] || [];
      groups[key].push(String(season.season));
    }));
    Object.keys(groups).forEach(url => {
      if (groups[url].length < 2) return;
      const seasons = Array.from(new Set(groups[url]));
      const across = seasons.length > 1;
      const permitted = !!serie.absolute || !!serie.allow_shared_url_across_seasons;
      issues.push({
        series: serie.id, level: permitted ? "info" : "warning",
        code: across ? "duplicate_url_across_seasons" : "duplicate_url_same_season",
        message: across ? "Duplicate URL across seasons." : "Duplicate URL in the same season.",
        seasons
      });
    });
    const statAudio = serie.seasons.some(season => season.urls.some(url => /(?:^|[-_.])ita(?:[-_.]|$)/i.test(url))) ? "DUB" : "SUB";
    const pref = serie.language_preference || serie.effective_language_preference || "AUTO";
    const mismatch = (pref.startsWith("SUB") && statAudio == "DUB") || (pref.startsWith("DUB") && statAudio == "SUB" && serie.seasons.some(season => season.urls.some(Boolean)));
    if (mismatch) issues.push({series: serie.id, level: "warning", code: "language_mismatch", message: "Language preference does not match detected audio."});
    serie.seasons.forEach(season => {
      if (urlOrderWarning(season.urls)) issues.push({series: serie.id, level: "warning", code: "url_order_warning", message: "URL order warning: part URLs look out of order.", seasons: [season.season]});
    });
  });
  return issues;
}

function serieStats(serie, issues) {
  const urls = serie.seasons.reduce((sum, season) => sum + season.urls.filter(Boolean).length, 0);
  const current = issues.filter(issue => issue.series == serie.id);
  const error = current.some(issue => issue.level == "error");
  const warning = current.some(issue => issue.level == "warning");
  const duplicateUrl = current.some(issue => issue.code == "duplicate_url_across_seasons" || issue.code == "duplicate_url_same_season");
  const languageMismatch = current.some(issue => issue.code == "language_mismatch");
  const orderWarning = current.some(issue => issue.code == "url_order_warning" || issue.code == "missing_base_url" || issue.code == "url_tba_warning");
  const status = error ? "invalid" : warning || serie.needs_review ? "needs_review" : urls ? "mapped" : "incomplete";
  const audio = serie.seasons.some(season => season.urls.some(url => /(?:^|[-_.])ita(?:[-_.]|$)/i.test(url))) ? "DUB" : (urls ? "SUB" : "UNKNOWN");
  return {seasons: serie.seasons.length, urls, status, error, warning, audio, duplicateUrl, languageMismatch, orderWarning};
}

function Badge({children, tone}) {
  return h("span", {className: "status-badge " + (tone || "")}, children);
}

function Icon({children}) {
  return h("span", {className: "material-icons icon-inline"}, children);
}

class AniDownDashboard extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      activeTab: "dashboard", series: [], drafts: [], aliases: {}, issues: [], loading: true, dirty: false,
      globalLanguage: "AUTO", search: "", reviewSearch: "", statusFilter: "all", expanded: {}, expandedSeasons: {},
      searchResults: {}, seasonPreviews: {}, importPreview: null, importMode: "add_only", importAutoSort: false, sonarrOpen: false, sonarrSeries: [], selectedSonarr: {}, sonarrSearch: "",
      selectedMappings: {}, bulkLanguage: "",
      runtime: {downloads_paused: false, search_only_mode: false, auto_save_high_confidence_mappings: true, auto_mapping_min_score: 0.95, log_panel_visible: true, compact_mode: false, log_verbosity: "COMPACT", auto_sort_part_urls_when_adding: true},
      searchIndex: {size: 0, age_hours: null}, sonarrSummary: null, refreshing: false, parseResult: null,
      parseOptions: {dry_run: false, apply_high_confidence: true, respect_tags: true, only_monitored: true, only_unmapped: true, include_already_mapped: false, force_rematch: false, include_future_tba: false, allow_shared_url_across_seasons: false, only_downloaded_in_sonarr: false},
      logEvents: [], rawLogs: "", logPaused: false, logAutoScroll: true, logSearch: "", logFilter: "all", scanStarting: false
    };
  }

  componentDidMount() {
    this.loadMappings();
    this.loadRuntime();
    this.loadOverview();
    this.loadLogs();
    this.logTimer = setInterval(() => { if (!this.state.logPaused) this.loadLogs(); }, 2000);
  }

  componentWillUnmount() { clearInterval(this.logTimer); }

  componentDidUpdate(previousProps, previousState) {
    if (this.state.logAutoScroll && previousState.logEvents != this.state.logEvents) {
      const list = document.querySelector(".live-log-dock .event-list");
      if (list) list.scrollTop = 0;
    }
  }

  loadMappings() {
    apiJson("/api/mapping/manager", {headers: {}}).then(data => {
      const series = rowsToSeries(data.rows, data.aliases);
      this.setState({series, drafts: data.drafts || [], aliases: data.aliases || {}, issues: validateSeries(series), globalLanguage: data.global_language_preference || "AUTO", loading: false, dirty: false, selectedMappings: {}});
    }).catch(error => { this.setState({loading: false}); showMessage(error.message); });
  }

  loadRuntime() {
    apiJson("/api/runtime/status", {headers: {}}).then(runtime => this.setState({runtime})).catch(error => showMessage(error.message));
  }

  loadOverview() {
    apiJson("/api/search-v3/index-status", {headers: {}}).then(data => this.setState({searchIndex: data.index || data})).catch(() => null);
    apiJson("/api/sonarr/unmapped-summary", {headers: {}}).then(sonarrSummary => this.setState({sonarrSummary})).catch(() => null);
  }

  loadLogs(raw = false) {
    const stamp = Date.now();
    apiJson("/api/logs/events?limit=300&_=" + stamp, {headers: {}, cache: "no-store"}).then(logEvents => this.setState({logEvents})).catch(() => null);
    if (raw || this.state.runtime.log_verbosity == "RAW") apiJson("/api/logs/raw?lines=500&_=" + stamp, {headers: {}, cache: "no-store"}).then(result => this.setState({rawLogs: result.text || ""})).catch(() => null);
  }

  setSeries(series, extra = {}) { this.setState(Object.assign({series, issues: validateSeries(series), dirty: true}, extra)); }

  updateRuntime(url, payload) {
    apiJson(url, {method: "POST", body: JSON.stringify(payload || {})}).then(runtime => { this.setState({runtime}); this.loadLogs(); }).catch(error => showMessage(error.message));
  }

  updateUiSetting(name, value) { this.updateRuntime("/api/runtime/ui-settings", {[name]: value}); }

  setGlobalLanguage(value) {
    apiJson("/api/mapping/preferences", {method: "POST", body: JSON.stringify({global_language_preference: value})})
      .then(() => { this.setState({globalLanguage: value}); this.loadMappings(); }).catch(error => showMessage(error.message));
  }

  updateSeries(id, patch) {
    this.setSeries(this.state.series.map(serie => {
      if (serie.id != id) return serie;
      const next = Object.assign({}, serie, patch);
      if (patch.absolute === true) next.seasons = next.seasons.slice(0, 1).map(season => Object.assign({}, season, {season: "absolute"}));
      if (patch.absolute === false) next.seasons = next.seasons.map(season => Object.assign({}, season, {season: season.season == "absolute" ? "1" : season.season}));
      return next;
    }));
  }

  updateSeason(id, index, patch) { this.setSeries(this.state.series.map(serie => serie.id == id ? Object.assign({}, serie, {seasons: serie.seasons.map((season, i) => i == index ? Object.assign({}, season, patch) : season)}) : serie)); }
  updateUrl(id, s, u, value) { this.setSeries(this.state.series.map(serie => serie.id == id ? Object.assign({}, serie, {seasons: serie.seasons.map((season, i) => i == s ? Object.assign({}, season, {urls: season.urls.map((url, j) => j == u ? value : url)}) : season)}) : serie)); }
  moveUrl(id, s, u, direction) { this.setSeries(this.state.series.map(serie => {
    if (serie.id != id) return serie;
    return Object.assign({}, serie, {seasons: serie.seasons.map((season, i) => {
      if (i != s) return season;
      const urls = season.urls.slice(), target = u + direction;
      if (target < 0 || target >= urls.length) return season;
      [urls[u], urls[target]] = [urls[target], urls[u]];
      return Object.assign({}, season, {urls});
    })});
  })); }
  sortSeasonUrls(id, s) { this.setSeries(this.state.series.map(serie => serie.id == id ? Object.assign({}, serie, {seasons: serie.seasons.map((season, i) => i == s ? Object.assign({}, season, {urls: sortPartUrls(season.urls)}) : season)}) : serie)); }
  addSeries(serie = blankSeries()) { this.setSeries([serie].concat(this.state.series), {expanded: Object.assign({}, this.state.expanded, {[serie.id]: true})}); }
  duplicateSeries(id) { const copy = JSON.parse(JSON.stringify(this.state.series.find(serie => serie.id == id))); copy.id = "copy-" + Date.now(); copy.title += " Copy"; copy.needs_review = true; this.addSeries(copy); }
  deleteSeries(id) { this.setSeries(this.state.series.filter(serie => serie.id != id)); }
  addSeason(id) { this.setSeries(this.state.series.map(serie => serie.id == id ? Object.assign({}, serie, {seasons: serie.seasons.concat([{season: serie.absolute ? "absolute" : String(serie.seasons.length + 1), urls: [""]}])}) : serie)); }
  removeSeason(id, index) { this.setSeries(this.state.series.map(serie => serie.id == id ? Object.assign({}, serie, {seasons: serie.seasons.filter((_, i) => i != index)}) : serie)); }
  addUrl(id, index) { this.setSeries(this.state.series.map(serie => serie.id == id ? Object.assign({}, serie, {seasons: serie.seasons.map((season, i) => i == index ? Object.assign({}, season, {urls: season.urls.concat([""])}) : season)}) : serie)); }
  removeUrl(id, s, u) { this.setSeries(this.state.series.map(serie => serie.id == id ? Object.assign({}, serie, {seasons: serie.seasons.map((season, i) => i == s ? Object.assign({}, season, {urls: season.urls.filter((_, j) => j != u)}) : season)}) : serie)); }

  selectedIds() { return Object.keys(this.state.selectedMappings).filter(id => this.state.selectedMappings[id]); }
  toggleSelected(id, checked) { this.setState({selectedMappings: Object.assign({}, this.state.selectedMappings, {[id]: checked})}); }
  selectVisible() {
    const selected = Object.assign({}, this.state.selectedMappings);
    this.filteredSeries().forEach(serie => selected[serie.id] = true);
    this.setState({selectedMappings: selected});
  }
  clearSelected() { this.setState({selectedMappings: {}}); }
  bulkAction(action, extra = {}) {
    if (this.state.dirty) return showMessage("Save or reload pending edits before a bulk action.");
    const ids = this.selectedIds();
    if (!ids.length) return;
    const titles = this.state.series.filter(serie => ids.includes(serie.id)).map(serie => serie.title).join(", ");
    if (action == "delete" && !window.confirm("Delete " + ids.length + " selected mappings?\n\n" + titles + "\n\nA backup will be created first.")) return;
    apiJson("/api/mapping/bulk-action", {method: "POST", body: JSON.stringify(Object.assign({action, mapping_ids: ids, confirm: action == "delete"}, extra))})
      .then(result => { showMessage(result.message); this.loadMappings(); this.loadLogs(); }).catch(error => showMessage(error.message));
  }
  clearReview(id) {
    if (this.state.dirty) return this.updateSeries(id, {needs_review: false});
    apiJson("/api/mapping/bulk-action", {method: "POST", body: JSON.stringify({action: "clear_needs_review", mapping_ids: [id]})})
      .then(result => { showMessage(result.message); this.loadMappings(); this.loadLogs(); }).catch(error => showMessage(error.message));
  }
  exportSelected() {
    const ids = new Set(this.selectedIds());
    const rows = seriesToRows(this.state.series.filter(serie => ids.has(serie.id)));
    const columns = ["sonarr_title", "season", "source_url", "absolute", "language_preference", "notes"];
    const csv = [columns.join(",")].concat(rows.map(row => columns.map(column => '"' + String(row[column] == null ? "" : row[column]).replaceAll('"', '""') + '"').join(","))).join("\n");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([csv], {type: "text/csv"}));
    link.download = "selected_mappings.csv"; link.click(); URL.revokeObjectURL(link.href);
  }

  save() {
    if (this.state.issues.some(issue => issue.level == "error")) return showMessage("Fix validation errors before saving.");
    apiJson("/api/mapping/bulk-save", {method: "POST", body: JSON.stringify({rows: seriesToRows(this.state.series), drafts: this.state.drafts})}).then(data => {
      Promise.all(this.state.series.map(serie => apiJson("/api/search-v3/aliases", {method: "POST", body: JSON.stringify({title: serie.title, aliases: serie.aliases || []})}).catch(() => null))).then(() => {
        showMessage(data.message || "Mappings saved."); this.loadMappings(); this.loadLogs();
      });
    }).catch(error => showMessage(error.message));
  }

  previewImport(file) {
    if (!file) return;
    const form = new FormData(); form.append("file", file, file.name); form.append("type", file.name.toLowerCase().endsWith(".csv") ? "csv" : "json");
    fetch("/api/mapping/import/preview", {method: "POST", body: form}).then(response => response.json()).then(payload => {
      if (payload.error) throw new Error(payload.error); this.setState({importPreview: payload.data});
    }).catch(error => showMessage(error.message));
  }

  applyImport() {
    if (this.state.importMode == "overwrite" && !window.confirm("Overwrite every existing mapping with this import? A backup will be created first.")) return;
    apiJson("/api/mapping/import/apply", {method: "POST", body: JSON.stringify({rows: this.state.importPreview.rows, mode: this.state.importMode, auto_sort_urls: this.state.importAutoSort})})
      .then(result => { showMessage(result.message); this.setState({importPreview: null}); this.loadMappings(); this.loadLogs(); }).catch(error => showMessage(error.message));
  }

  searchMapping(serie, debug = false, forceRematch = false) {
    const season = serie.seasons[0] ? serie.seasons[0].season : "1";
    apiJson("/api/search-v3/search", {method: "POST", body: JSON.stringify({title: serie.title, season: season == "absolute" ? 1 : season, language_preference: serie.language_preference || "AUTO", debug, force_rematch: forceRematch})})
      .then(result => { this.setState({searchResults: Object.assign({}, this.state.searchResults, {[serie.id]: result})}); this.loadLogs(); }).catch(error => showMessage(error.message));
  }

  pickSearchResult(serie, match, seasonIndex) {
    const series = this.state.series.map(item => {
      if (item.id != serie.id) return item;
      const seasons = item.seasons.slice(), index = Number(seasonIndex) || 0;
      seasons[index] = Object.assign({}, seasons[index], {urls: [match.url].concat(seasons[index].urls.filter(Boolean).filter(url => url != match.url))});
      return Object.assign({}, item, {seasons, needs_review: match.confidence != "high"});
    });
    this.setSeries(series);
  }

  addAlias(serie, match) { this.updateSeries(serie.id, {aliases: (serie.aliases || []).concat([match.title]).filter((value, i, all) => value && all.indexOf(value) == i)}); }

  runScanNow() {
    this.setState({scanStarting: true});
    apiJson("/api/runtime/run-scan-now", {method: "POST", body: JSON.stringify({})}).then(result => {
      showMessage(result.message || "Manual scan started");
      this.setState({scanStarting: false});
      this.loadLogs(true);
      setTimeout(() => this.loadLogs(true), 1200);
    }).catch(error => { this.setState({scanStarting: false}); showMessage(error.message); this.loadLogs(true); });
  }

  previewSeasonOrder(serie, season) {
    apiJson("/api/mapping/preview-season-order", {method: "POST", body: JSON.stringify({title: serie.title, season: season.season})}).then(result => {
      this.setState({seasonPreviews: Object.assign({}, this.state.seasonPreviews, {[serie.id + ":" + season.season]: result})});
      this.loadLogs();
    }).catch(error => showMessage(error.message));
  }

  parseSonarr() {
    this.setState({refreshing: true});
	apiJson("/api/sonarr/parse-unmapped/start", {method: "POST", body: JSON.stringify(this.state.parseOptions)}).then(job => {
	  this.pollSonarrParse(job.job_id);
	}).catch(error => { this.setState({refreshing: false}); showMessage(error.message); });
  }

  pollSonarrParse(jobId) {
	apiJson("/api/sonarr/parse-unmapped/status/" + encodeURIComponent(jobId)).then(job => {
	  if (job.status == "completed") {
		this.setState({refreshing: false, parseResult: job.result, activeTab: "parser"});
		if (!this.state.parseOptions.dry_run) this.loadMappings();
		this.loadOverview(); this.loadLogs(true);
		return;
	  }
	  if (job.status == "failed") throw new Error(job.error || "Sonarr parse failed");
	  window.setTimeout(() => this.pollSonarrParse(jobId), 1500);
	}).catch(error => { this.setState({refreshing: false}); showMessage(error.message); this.loadLogs(true); });
  }

  refreshIndex() {
    apiJson("/api/search-v3/refresh-index", {method: "POST", body: JSON.stringify({})}).then(() => {
      showMessage("Search V3 index refreshed.");
      this.loadOverview(); this.loadLogs();
    }).catch(error => showMessage(error.message));
  }

  downloadParseReport() {
    if (!this.state.parseResult) return;
    const blob = new Blob([JSON.stringify(this.state.parseResult, null, 2)], {type: "application/json"});
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "sonarr_parse_report.json";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  loadSonarrDrafts() {
    apiJson("/api/mapping/sonarr/series", {headers: {}}).then(sonarrSeries => this.setState({sonarrSeries, sonarrOpen: true})).catch(error => showMessage(error.message));
  }

  addSelectedSonarr() {
    const additions = this.state.sonarrSeries.filter(serie => this.state.selectedSonarr[serie.id] && !serie.mapped).map(serie => Object.assign(blankSeries(), {id: "sonarr-" + serie.id, title: serie.title, aliases: serie.aliases || [], notes: "Draft from Sonarr importer."}));
    this.setSeries(additions.concat(this.state.series), {sonarrOpen: false, selectedSonarr: {}});
  }

  reviewDraftGroups() {
    const grouped = {};
    (this.state.drafts || []).filter(draft => draft && draft.needs_review !== false).forEach((draft, index) => {
      const key = normalize(draft.sonarr_title || "") + "::" + String(draft.season || "1");
      const current = grouped[key];
      if (!current || String(draft.created_at || "") >= String(current.created_at || "")) grouped[key] = Object.assign({_draft_key: key, _draft_index: index}, draft);
    });
    return Object.values(grouped).sort((a, b) => String(a.sonarr_title || "").localeCompare(String(b.sonarr_title || "")) || Number(a.season || 0) - Number(b.season || 0));
  }

  remainingDraftsAfter(draft) {
    const title = normalize(draft.sonarr_title || ""), season = String(draft.season || "1");
    return (this.state.drafts || []).filter(item => normalize(item.sonarr_title || "") != title || String(item.season || "1") != season);
  }

  dismissReviewDraft(draft) {
    this.setState({drafts: this.remainingDraftsAfter(draft), dirty: true});
    showMessage("Review draft dismissed. Save changes to persist.");
  }

  applyReviewDraft(draft) {
    const title = String(draft.sonarr_title || "").trim(), season = String(draft.season || "1"), url = String(draft.source_url || "").trim();
    if (!title || !url) return showMessage("This review draft has no usable title or URL.");
    let found = false, targetId = null;
    let series = this.state.series.map(item => {
      if (normalize(item.title) != normalize(title)) return item;
      found = true; targetId = item.id;
      const seasons = item.seasons.slice();
      const index = seasons.findIndex(value => String(value.season) == season);
      if (index < 0) seasons.push({season, urls: [url]});
      else {
        const urls = seasons[index].urls.filter(Boolean).filter(value => value != url);
        seasons[index] = Object.assign({}, seasons[index], {urls: [url].concat(urls)});
      }
      return Object.assign({}, item, {seasons: sortSeasons(seasons), needs_review: false, notes: item.notes || draft.notes || ""});
    });
    if (!found) {
      const next = blankSeries();
      next.title = title; next.seasons = [{season, urls: [url]}]; next.language_preference = draft.language_preference || ""; next.notes = draft.notes || ""; next.needs_review = false;
      targetId = next.id; series = [next].concat(series);
    }
    this.setSeries(series, {drafts: this.remainingDraftsAfter(draft), expanded: Object.assign({}, this.state.expanded, {[targetId]: true})});
    showMessage("Review draft applied. Check the mapping, then Save changes.");
  }

  renderReview() {
    const query = String(this.state.reviewSearch || "").toLowerCase();
    const drafts = this.reviewDraftGroups().filter(draft => !query || JSON.stringify(draft).toLowerCase().includes(query));
    return h("div", {className: "view review-view"},
      h(ViewHeading, {title: "Needs Review", text: "Items AniDown could not decide safely. Completed seasons stay visible here when a review was recorded."}),
      h("div", {className: "review-toolbar"},
        h("input", {type: "search", placeholder: "Search reviews", value: this.state.reviewSearch, onChange: e => this.setState({reviewSearch: e.target.value})}),
        h(Badge, {tone: drafts.length ? "warning" : "ok"}, drafts.length + " pending"),
        this.state.dirty && h("button", {className: "btn text primary", onClick: () => this.save()}, "Save changes")
      ),
      drafts.length ? h("div", {className: "review-list"}, drafts.map(draft => {
        const candidates = (draft.review_candidates || []).filter(candidate => candidate && candidate.title);
        const alternatives = candidates.filter(candidate => candidate.title != draft.source_title || candidate.url != draft.source_url);
        const score = draft.search_score == null ? null : Number(draft.search_score).toFixed(2);
        return h("article", {className: "surface review-card", key: draft._draft_key},
          h("div", {className: "review-main"},
            h("div", {className: "review-heading"},
              h("strong", null, draft.sonarr_title || "Untitled"),
              h(Badge, null, "S" + (draft.season || "1")),
              draft.search_confidence && h(Badge, {tone: draft.search_confidence == "high" ? "ok" : "warning"}, draft.search_confidence)
            ),
            h("div", {className: "review-candidate-line"},
              h("span", {className: "review-label"}, "Candidate"),
              draft.source_url ? h("a", {href: draft.source_url, target: "_blank", rel: "noopener"}, draft.source_title || draft.source_url) : h("span", null, draft.source_title || "No candidate"),
              score && h("span", {className: "review-score"}, "score " + score),
              draft.detected_language && h(Badge, {tone: draft.detected_language == "DUB" ? "audio-dub" : draft.detected_language == "SUB" ? "audio-sub" : "audio-unknown"}, draft.detected_language)
            ),
            h("p", {className: "review-reason"}, h("strong", null, "Why: "), draft.review_reason || draft.notes || "Review required"),
            alternatives.length > 0 && h("details", {className: "review-alternatives"},
              h("summary", null, alternatives.length + " other candidate" + (alternatives.length == 1 ? "" : "s")),
              h("div", {className: "review-alt-list"}, alternatives.map((candidate, index) => h("div", {className: "review-alt", key: (candidate.url || candidate.title) + index},
                candidate.url ? h("a", {href: candidate.url, target: "_blank", rel: "noopener"}, candidate.title) : h("span", null, candidate.title),
                h("span", null, candidate.score == null ? "" : "score " + Number(candidate.score).toFixed(2)),
                candidate.audio && h(Badge, {tone: candidate.audio == "DUB" ? "audio-dub" : candidate.audio == "SUB" ? "audio-sub" : "audio-unknown"}, candidate.audio)
              )))
            )
          ),
          h("div", {className: "review-actions"},
            h("button", {className: "btn text primary", onClick: () => this.applyReviewDraft(draft)}, "Apply"),
            h("button", {className: "btn text", onClick: () => this.dismissReviewDraft(draft)}, "Dismiss")
          )
        );
      })) : h("section", {className: "surface empty-state"}, "No pending reviews.")
    );
  }

  filteredSeries() {
    return this.state.series.filter(serie => {
      const text = [serie.title, serie.notes, (serie.aliases || []).join(" "), JSON.stringify(serie.seasons)].join(" ").toLowerCase();
      const status = serieStats(serie, this.state.issues);
      const skippedFuture = (this.state.parseResult && this.state.parseResult.items || []).some(item => normalize(item.title) == normalize(serie.title) && item.status == "skipped_future_tba");
      if (this.state.search && !text.includes(this.state.search.toLowerCase())) return false;
      if (this.state.statusFilter == "review") return status.status == "needs_review" || status.error || status.duplicateUrl || status.languageMismatch || status.orderWarning || skippedFuture;
      if (this.state.statusFilter == "incomplete") return status.status != "mapped";
      if (this.state.statusFilter == "multi") return status.seasons > 1;
      if (this.state.statusFilter == "duplicate") return status.duplicateUrl;
      if (this.state.statusFilter == "invalid") return status.error;
      if (this.state.statusFilter == "language") return status.languageMismatch;
      if (this.state.statusFilter == "future") return skippedFuture;
      if (this.state.statusFilter == "unmapped") return status.urls == 0;
      return true;
    });
  }

  setAllExpanded(value) { const expanded = {}; this.state.series.forEach(serie => expanded[serie.id] = value); this.setState({expanded}); }

  renderTopbar() {
    return h("header", {className: "app-topbar"},
      h("div", {className: "brand"}, h(Icon, null, "movie_filter"), h("strong", null, "AniDown v3")),
      h("div", {className: "top-status"},
        h(Badge, {tone: this.state.runtime.search_only_mode ? "warning" : "ok"}, this.state.runtime.search_only_mode ? "Search-only" : "Running"),
        h("button", {className: "btn text primary", disabled: this.state.scanStarting, onClick: () => this.runScanNow()}, this.state.scanStarting ? "Starting scan..." : "Run scan now"),
        h("button", {className: "btn text " + (this.state.runtime.downloads_paused ? "active" : ""), onClick: () => this.updateRuntime(this.state.runtime.downloads_paused ? "/api/runtime/resume-downloads" : "/api/runtime/pause-downloads", {})}, this.state.runtime.downloads_paused ? "Resume downloads" : "Pause downloads"),
        h("label", {className: "switch"}, h("input", {type: "checkbox", checked: !!this.state.runtime.search_only_mode, onChange: e => this.updateRuntime("/api/runtime/search-only-mode", {enabled: e.target.checked})}), "Search-only")
      )
    );
  }

  renderSidebar() {
    const reviewCount = this.reviewDraftGroups().length;
    return h("aside", {className: "app-sidebar"},
      h("nav", null, TABS.map(tab => h("button", {key: tab[0], className: this.state.activeTab == tab[0] ? "nav-item active" : "nav-item", onClick: () => this.setState({activeTab: tab[0]})}, h(Icon, null, tab[1]), h("span", {className: "nav-label"}, tab[2]), tab[0] == "review" && reviewCount > 0 && h(Badge, {tone: "warning"}, reviewCount)))),
      h("div", {className: "sidebar-foot"}, h(Badge, null, this.state.series.length + " mappings"))
    );
  }

  renderDashboard() {
    const all = this.state.series.map(serie => serieStats(serie, this.state.issues));
    const mapped = all.filter(stat => stat.status == "mapped").length, review = all.filter(stat => stat.status == "needs_review").length, reviewDrafts = this.reviewDraftGroups().length;
    const sonarr = this.state.sonarrSummary || {};
    return h("div", {className: "view dashboard-view"},
      h(ViewHeading, {title: "Dashboard", text: "Status and latest mapping activity."}),
      h("div", {className: "summary-grid"},
        h(SummaryCard, {title: "Runtime", value: this.state.runtime.downloads_paused ? "Downloads paused" : "Running", tone: this.state.runtime.downloads_paused ? "warning" : "ok"}, this.state.runtime.search_only_mode ? "Search-only mode active" : "Downloads enabled"),
        h(SummaryCard, {title: "Mappings", value: this.state.series.length}, mapped + " mapped OK / " + (review + reviewDrafts) + " need review" + (reviewDrafts ? " (" + reviewDrafts + " drafts)" : "")),
        h(SummaryCard, {title: "Search index", value: this.state.searchIndex.size || 0}, "Age: " + (this.state.searchIndex.age_hours == null ? "-" : Number(this.state.searchIndex.age_hours).toFixed(1) + "h"), h("button", {className: "link-action", onClick: () => this.refreshIndex()}, "Refresh")),
        h(SummaryCard, {title: "Sonarr", value: sonarr.total_series == null ? "-" : sonarr.total_series}, (sonarr.unmapped || 0) + " unmapped seasons", h("button", {className: "link-action", onClick: () => this.setState({activeTab: "parser"})}, "Open parser"))
      ),
      h("section", {className: "surface activity-surface"}, h("h3", null, "Latest activity"), h(EventRows, {events: this.state.logEvents.slice(0, 10), verbosity: "COMPACT"}))
    );
  }

  renderMappingToolbar() {
    return h("div", {className: "mapping-actionbar"},
      h("input", {type: "search", placeholder: "Search mappings, aliases or URLs", value: this.state.search, onChange: e => this.setState({search: e.target.value})}),
      h("select", {value: this.state.statusFilter, onChange: e => this.setState({statusFilter: e.target.value})},
        h("option", {value: "all"}, "All mappings"),
        h("option", {value: "review"}, "Mapping issues"),
        h("option", {value: "incomplete"}, "Only incomplete"),
        h("option", {value: "multi"}, "Only multi-season"),
        h("option", {value: "duplicate"}, "Duplicate URLs"),
        h("option", {value: "invalid"}, "Invalid"),
        h("option", {value: "language"}, "Language mismatch"),
        h("option", {value: "unmapped"}, "Unmapped"),
        h("option", {value: "future"}, "Future/TBA skipped")
      ),
      h("button", {className: "btn text", onClick: () => this.selectVisible()}, "Select visible"),
      h("button", {className: "btn text", onClick: () => this.clearSelected()}, "Deselect all"),
      h("button", {className: "btn text", onClick: () => this.addSeries()}, "Add mapping"),
      h("button", {className: "btn text", onClick: () => this.setAllExpanded(true)}, "Expand all"),
      h("button", {className: "btn text", onClick: () => this.setAllExpanded(false)}, "Collapse all"),
      h("button", {className: "btn text primary", onClick: () => this.save()}, this.state.dirty ? "Save changes" : "Bulk save"),
      h("button", {className: "btn text", onClick: () => this.setState({activeTab: "parser"})}, "Parse unmapped from Sonarr"),
      h("button", {className: "btn text", onClick: () => this.loadSonarrDrafts()}, "Import Sonarr drafts"),
      h("button", {className: "btn text", onClick: () => this.refreshIndex()}, "Refresh index"),
      h("a", {className: "btn text", href: "/api/mapping/export/json"}, "Export JSON"),
      h("a", {className: "btn text", href: "/api/mapping/export/csv"}, "Export CSV"),
      h("label", {className: "btn text file-button"}, "Import", h("input", {type: "file", accept: ".json,.csv", onChange: e => this.previewImport(e.target.files[0])}))
    );
  }

  renderMapping() {
    const selectedCount = this.selectedIds().length;
    return h("div", {className: "view mapping-view"},
      h(ViewHeading, {title: "Mapping Manager", text: "Fast multi-season editing with table.json compatible saves."}),
      this.renderMappingToolbar(), selectedCount > 0 && h("div", {className: "bulk-actionbar"},
        h("strong", null, selectedCount + " selected"),
        h("button", {className: "btn text", onClick: () => this.bulkAction("clear_review")}, "Clear needs review"),
        h("select", {value: this.state.bulkLanguage, onChange: e => this.setState({bulkLanguage: e.target.value})}, LANGUAGES.map(value => h("option", {key: value, value}, value || "Use global default"))),
        h("button", {className: "btn text", onClick: () => this.bulkAction("set_language", {language_preference: this.state.bulkLanguage})}, "Set language"),
        h("button", {className: "btn text", onClick: () => this.exportSelected()}, "Export selected"),
        h("button", {className: "btn text danger", onClick: () => this.bulkAction("delete")}, "Delete selected"),
        h("button", {className: "btn text", onClick: () => this.clearSelected()}, "Cancel")
      ), this.renderImportPreview(), this.renderSonarrDrafts(),
      h("div", {className: "series-list"}, this.filteredSeries().map(serie => h(SeriesCard, {
        key: serie.id, serie, issues: this.state.issues, expanded: !!this.state.expanded[serie.id], expandedSeasons: this.state.expandedSeasons,
        selected: !!this.state.selectedMappings[serie.id], onSelect: checked => this.toggleSelected(serie.id, checked),
        result: this.state.searchResults[serie.id],
        futureSkipped: (this.state.parseResult && this.state.parseResult.items || []).some(item => normalize(item.title) == normalize(serie.title) && item.status == "skipped_future_tba"),
        onToggle: () => this.setState({expanded: Object.assign({}, this.state.expanded, {[serie.id]: !this.state.expanded[serie.id]})}),
        onToggleSeason: key => this.setState({expandedSeasons: Object.assign({}, this.state.expandedSeasons, {[key]: !this.state.expandedSeasons[key]})}),
        onChange: patch => this.updateSeries(serie.id, patch), onSeasonChange: (i, patch) => this.updateSeason(serie.id, i, patch),
        onUrlChange: (s, u, value) => this.updateUrl(serie.id, s, u, value), onAddSeason: () => this.addSeason(serie.id),
        onRemoveSeason: i => this.removeSeason(serie.id, i), onAddUrl: i => this.addUrl(serie.id, i), onRemoveUrl: (s, u) => this.removeUrl(serie.id, s, u),
        onMoveUrl: (s, u, direction) => this.moveUrl(serie.id, s, u, direction), onSortUrls: s => this.sortSeasonUrls(serie.id, s),
        onSaveOrder: () => this.save(), onResetOrder: () => this.loadMappings(), onPreviewOrder: season => this.previewSeasonOrder(serie, season),
        seasonPreviews: this.state.seasonPreviews,
        onDuplicate: () => this.duplicateSeries(serie.id), onDelete: () => this.deleteSeries(serie.id),
        onClearReview: () => this.clearReview(serie.id),
        onSearch: () => this.searchMapping(serie), onDebug: () => this.searchMapping(serie, true), onRematch: () => this.searchMapping(serie, true, true),
        onCopy: () => navigator.clipboard.writeText((serie.seasons.flatMap(season => season.urls).find(Boolean) || "")),
        onPick: (match, index) => this.pickSearchResult(serie, match, index), onAlias: match => this.addAlias(serie, match)
      })))
    );
  }

  renderParser() {
    const result = this.state.parseResult;
    const options = [["dry_run", "Dry run / preview only"], ["apply_high_confidence", "Apply high-confidence matches"], ["respect_tags", "Respect AniDown tags"], ["only_monitored", "Only monitored series"], ["only_unmapped", "Only unmapped series"], ["include_already_mapped", "Include already mapped series"], ["force_rematch", "Force rematch already mapped series"], ["include_future_tba", "Include future/TBA seasons"], ["allow_shared_url_across_seasons", "Allow shared URL across seasons"]];
    return h("div", {className: "view parser-view"},
      h(ViewHeading, {title: "Sonarr Parser", text: "Metadata-only mapping pass. This action never starts downloads."}),
      h("section", {className: "surface parser-controls"},
        h("div", {className: "option-grid"}, options.map(option => h("label", {key: option[0], className: "switch option"}, h("input", {type: "checkbox", checked: !!this.state.parseOptions[option[0]], onChange: e => this.setState({parseOptions: Object.assign({}, this.state.parseOptions, {[option[0]]: e.target.checked})})}), option[1]))),
        h("button", {className: "btn text primary", disabled: this.state.refreshing, onClick: () => this.parseSonarr()}, this.state.refreshing ? "Parsing..." : "Parse unmapped from Sonarr"),
        h("button", {className: "btn text", onClick: () => this.refreshIndex()}, "Refresh Search Index"),
        h("p", {className: "quiet"}, "Safe defaults: only unmapped ON, force rematch OFF, future/TBA OFF, shared season URLs OFF.")
      ),
      result ? h(ParserReport, {result}) : h("section", {className: "surface empty-state"}, "Run a parse to see skipped existing mappings and new Search V3 matches.")
    );
  }

  renderReport() {
    return h("div", {className: "view report-view"}, h(ViewHeading, {title: "Search-only Report", text: "Latest parser result with technical details kept expandable."}), this.state.parseResult ? h("div", null, h("button", {className: "btn text", onClick: () => this.downloadParseReport()}, "Download latest JSON"), h(ParserReport, {result: this.state.parseResult})) : h("section", {className: "surface empty-state"}, "No parser report in this session."));
  }

  renderSettings() {
    const r = this.state.runtime;
    return h("div", {className: "view settings-view"}, h(ViewHeading, {title: "Settings", text: "Runtime and dashboard preferences are stored outside table.json."}),
      h("section", {className: "surface settings-grid"},
        h("label", null, "Global language preference", h("select", {value: this.state.globalLanguage, onChange: e => this.setGlobalLanguage(e.target.value)}, LANGUAGES.filter(Boolean).map(value => h("option", {key: value, value}, value)))),
        h("label", {className: "switch"}, h("input", {type: "checkbox", checked: !!r.downloads_paused, onChange: e => this.updateRuntime(e.target.checked ? "/api/runtime/pause-downloads" : "/api/runtime/resume-downloads", {})}), "Pause downloads"),
        h("label", {className: "switch"}, h("input", {type: "checkbox", checked: !!r.search_only_mode, onChange: e => this.updateRuntime("/api/runtime/search-only-mode", {enabled: e.target.checked})}), "Search-only mode"),
        h("label", {className: "switch"}, h("input", {type: "checkbox", checked: !!r.auto_save_high_confidence_mappings, onChange: e => this.updateRuntime("/api/runtime/auto-save-mappings", {enabled: e.target.checked})}), "Auto-save high confidence"),
        h("label", null, "Auto mapping threshold", h("input", {type: "number", step: "0.01", min: "0.5", max: "1", value: r.auto_mapping_min_score, onChange: e => this.updateRuntime("/api/runtime/auto-save-mappings", {enabled: r.auto_save_high_confidence_mappings, auto_mapping_min_score: e.target.value})})),
        h("label", null, "Log verbosity", h("select", {value: r.log_verbosity || "COMPACT", onChange: e => { this.updateUiSetting("log_verbosity", e.target.value); this.loadLogs(true); }}, ["COMPACT", "NORMAL", "DEBUG", "RAW"].map(value => h("option", {key: value, value}, value)))),
        h("label", {className: "switch"}, h("input", {type: "checkbox", checked: r.log_panel_visible !== false, onChange: e => this.updateUiSetting("log_panel_visible", e.target.checked)}), "Show log panel"),
        h("label", {className: "switch"}, h("input", {type: "checkbox", checked: !!r.compact_mode, onChange: e => this.updateUiSetting("compact_mode", e.target.checked)}), "Compact mode"),
        h("label", {className: "switch"}, h("input", {type: "checkbox", checked: !!r.auto_sort_part_urls_when_adding, onChange: e => this.updateUiSetting("auto_sort_part_urls_when_adding", e.target.checked)}), "Auto-sort part URLs when adding new URL")
      )
    );
  }

  renderLogs() {
    return h("div", {className: "view logs-view"}, h(ViewHeading, {title: "Logs", text: "Readable operations with expandable raw diagnostic context."}), h(LogPanel, this.logProps(true)));
  }

  logProps(full) {
    return {
      full, events: this.state.logEvents, raw: this.state.rawLogs, filter: this.state.logFilter, search: this.state.logSearch,
      verbosity: this.state.runtime.log_verbosity || "COMPACT", paused: this.state.logPaused, autoScroll: this.state.logAutoScroll,
      onSearch: value => this.setState({logSearch: value}), onFilter: value => this.setState({logFilter: value}),
      onPause: value => this.setState({logPaused: value}), onAutoScroll: value => this.setState({logAutoScroll: value}),
      onClear: () => this.setState({logEvents: [], rawLogs: ""}), onRefresh: () => this.loadLogs(true),
      onVerbosity: value => { this.updateUiSetting("log_verbosity", value); this.loadLogs(true); },
      onCopy: () => navigator.clipboard.writeText(this.visibleLogText()), onCopyRaw: () => { apiJson("/api/logs/raw?lines=1000", {headers: {}}).then(data => navigator.clipboard.writeText(data.text)); }
    };
  }

  visibleLogText() { return this.state.logEvents.map(event => event.timestamp + " " + event.compact).join("\n"); }

  renderView() {
    if (this.state.activeTab == "mapping") return this.renderMapping();
    if (this.state.activeTab == "review") return this.renderReview();
    if (this.state.activeTab == "parser") return this.renderParser();
    if (this.state.activeTab == "report") return this.renderReport();
    if (this.state.activeTab == "settings") return this.renderSettings();
    if (this.state.activeTab == "logs") return this.renderLogs();
    return this.renderDashboard();
  }

  renderImportPreview() {
    if (!this.state.importPreview) return null;
    const errors = (this.state.importPreview.issues || []).filter(issue => issue.level == "error").length;
    const stats = (this.state.importPreview.summaries || {})[this.state.importMode] || {added: 0, updated: 0, deleted: 0, unchanged: 0};
    return h("section", {className: "surface import-preview"}, h("h3", null, "Import preview"), h("p", null, this.state.importPreview.rows.length + " rows found. " + errors + " blocking errors."),
      h("label", {className: "import-mode"}, "Import mode", h("select", {value: this.state.importMode, onChange: e => this.setState({importMode: e.target.value})},
        h("option", {value: "add_only"}, "Merge / add only"),
        h("option", {value: "update"}, "Merge and update existing"),
        h("option", {value: "overwrite"}, "Overwrite all mappings")
      )),
      h("label", {className: "import-mode"}, h("input", {type: "checkbox", checked: !this.state.importAutoSort, onChange: e => this.setState({importAutoSort: !e.target.checked})}), "Preserve imported URL order"),
      h("label", {className: "import-mode"}, h("input", {type: "checkbox", checked: !!this.state.importAutoSort, onChange: e => this.setState({importAutoSort: e.target.checked})}), "Auto-sort part URLs after import"),
      h("div", {className: "import-stats"}, [["Added", stats.added], ["Updated", stats.updated], ["Deleted", stats.deleted], ["Unchanged", stats.unchanged]].map(stat => h(Badge, {key: stat[0], tone: stat[0] == "Deleted" && stat[1] ? "warning" : ""}, stat[0] + ": " + stat[1]))),
      h("button", {className: "btn text primary", disabled: errors > 0, onClick: () => this.applyImport()}, "Apply import"), h("button", {className: "btn text", onClick: () => this.setState({importPreview: null})}, "Cancel"),
      h("div", {className: "table-wrap"}, h("table", {className: "report-table"}, h("thead", null, h("tr", null, ["Title", "Season", "URL"].map(label => h("th", {key: label}, label)))), h("tbody", null, this.state.importPreview.rows.slice().sort((a, b) => String(a.sonarr_title).localeCompare(String(b.sonarr_title)) || Number(a.season) - Number(b.season)).slice(0, 20).map((row, index) => h("tr", {key: index}, h("td", null, row.sonarr_title), h("td", null, row.season), h("td", null, row.source_url || "-")))))));
  }

  renderSonarrDrafts() {
    return this.state.sonarrOpen ? h("section", {className: "surface sonarr-drafts"}, h("div", {className: "surface-title"}, h("h3", null, "Create manual Sonarr drafts"), h("button", {className: "btn text", onClick: () => this.setState({sonarrOpen: false})}, "Close")),
      h("input", {type: "search", placeholder: "Filter Sonarr", value: this.state.sonarrSearch, onChange: e => this.setState({sonarrSearch: e.target.value})}),
      h("div", {className: "sonarr-list"}, this.state.sonarrSeries.filter(serie => !this.state.sonarrSearch || serie.title.toLowerCase().includes(this.state.sonarrSearch.toLowerCase())).map(serie => h("label", {key: serie.id, className: "sonarr-row"}, h("input", {type: "checkbox", disabled: serie.mapped, onChange: e => this.setState({selectedSonarr: Object.assign({}, this.state.selectedSonarr, {[serie.id]: e.target.checked})})}), serie.title, h(Badge, {tone: serie.mapped ? "ok" : "warning"}, serie.mapped ? "mapped" : "draft")))),
      h("button", {className: "btn text primary", onClick: () => this.addSelectedSonarr()}, "Add selected drafts")) : null;
  }

  render() {
    if (this.state.loading) return h("div", {className: "app-loading"}, "Loading AniDown v3...");
    return h("div", {className: "anidown-app " + (this.state.runtime.compact_mode ? "compact" : "")},
      this.renderTopbar(), this.renderSidebar(),
      h("main", {className: "app-content"}, this.renderView()),
      this.state.runtime.log_panel_visible !== false && this.state.activeTab != "logs" ? h("aside", {className: "live-log-dock"}, h(LogPanel, this.logProps(false))) : null
    );
  }
}

function ViewHeading({title, text}) { return h("div", {className: "view-heading"}, h("div", null, h("h1", null, title), h("p", null, text))); }
function SummaryCard({title, value, tone, children}) { return h("section", {className: "summary-card " + (tone || "")}, h("small", null, title), h("strong", null, value), h("div", {className: "summary-detail"}, children)); }

function SeriesCard(props) {
  const serie = props.serie, stat = serieStats(serie, props.issues), pref = serie.language_preference || ("global " + serie.effective_language_preference);
  const firstUrl = serie.seasons.flatMap(season => season.urls).find(Boolean);
  return h("section", {className: "series-card " + stat.status},
    h("div", {className: "series-header"},
      h("label", {className: "select-mapping", title: "Select mapping"}, h("input", {type: "checkbox", checked: props.selected, onChange: e => props.onSelect(e.target.checked)})),
      h("button", {className: "series-summary", onClick: props.onToggle}, h(Icon, null, props.expanded ? "expand_more" : "chevron_right"), h("span", {className: "series-name"}, serie.title || "Untitled mapping"), h(Badge, null, "Seasons: " + stat.seasons), h(Badge, null, "URLs: " + stat.urls), h(Badge, null, "Pref: " + pref), h(Badge, {tone: stat.audio == "DUB" ? "audio-dub" : stat.audio == "SUB" ? "audio-sub" : "audio-unknown"}, "Audio: " + stat.audio), stat.duplicateUrl && h(Badge, {tone: "warning"}, "duplicate URL"), stat.orderWarning && h(Badge, {tone: "warning"}, "URL order warning"), stat.languageMismatch && h(Badge, {tone: "warning"}, "language mismatch"), props.futureSkipped && h(Badge, {tone: "warning"}, "future/TBA skipped"), h(Badge, {tone: stat.error ? "error" : stat.warning || serie.needs_review ? "warning" : "ok"}, stat.status.replace("_", " ")))
    ),
    props.expanded && h("div", {className: "series-body"},
      h("div", {className: "series-fields"},
        h("label", null, "Title", h("input", {value: serie.title, onChange: e => props.onChange({title: e.target.value})})),
        h("label", null, "Language", h("select", {value: serie.language_preference || "", onChange: e => props.onChange({language_preference: e.target.value})}, LANGUAGES.map(value => h("option", {value, key: value}, value || "Use global default")))),
        h("label", null, "Aliases", h("input", {value: (serie.aliases || []).join(", "), onChange: e => props.onChange({aliases: e.target.value.split(",").map(text => text.trim()).filter(Boolean)})})),
        h("label", null, "Notes", h("input", {value: serie.notes || "", onChange: e => props.onChange({notes: e.target.value})})),
        h("label", {className: "switch inline-switch"}, h("input", {type: "checkbox", checked: !!serie.absolute, onChange: e => props.onChange({absolute: e.target.checked})}), "Absolute"),
        h("label", {className: "switch inline-switch"}, h("input", {type: "checkbox", checked: !!serie.needs_review, onChange: e => props.onChange({needs_review: e.target.checked})}), "Needs review"),
        h("label", {className: "switch inline-switch"}, h("input", {type: "checkbox", checked: !!serie.allow_shared_url_across_seasons, onChange: e => props.onChange({allow_shared_url_across_seasons: e.target.checked})}), "Allow same URL across seasons")
      ),
      h("div", {className: "series-actions"},
        h("button", {className: "btn text", onClick: props.onSearch}, "Search V3"), h("button", {className: "btn text", onClick: props.onDebug}, "Debug Search"), h("button", {className: "btn text", onClick: props.onRematch}, "Rematch language"),
        h("button", {className: "btn text", disabled: !firstUrl, onClick: props.onCopy}, "Copy URL"), firstUrl && h("a", {className: "btn text", href: firstUrl, target: "_blank", rel: "noopener"}, "Open URL"),
        h("button", {className: "btn text", onClick: props.onClearReview}, "Clear review"), h("button", {className: "btn text", onClick: props.onAddSeason}, "Add season"), h("button", {className: "btn text", onClick: props.onDuplicate}, "Duplicate"), h("button", {className: "btn text danger", onClick: props.onDelete}, "Delete")
      ),
      props.result && h(SearchResults, {result: props.result, seasons: serie.seasons, onPick: props.onPick, onAlias: props.onAlias}),
      h("div", {className: "season-list"}, sortSeasons(serie.seasons).map(season => {
        const index = serie.seasons.indexOf(season);
        const key = serie.id + ":" + season.season;
        return h(SeasonPanel, {key, season, index, absolute: serie.absolute, expanded: !!props.expandedSeasons[key], preview: props.seasonPreviews && props.seasonPreviews[key], onToggle: () => props.onToggleSeason(key), onChange: patch => props.onSeasonChange(index, patch), onUrlChange: (u, value) => props.onUrlChange(index, u, value), onAddUrl: () => props.onAddUrl(index), onRemoveUrl: u => props.onRemoveUrl(index, u), onMoveUrl: (u, direction) => props.onMoveUrl(index, u, direction), onSortUrls: () => props.onSortUrls(index), onSaveOrder: props.onSaveOrder, onResetOrder: props.onResetOrder, onPreviewOrder: () => props.onPreviewOrder(season), onRemove: () => props.onRemoveSeason(index)});
      })),
      h("div", {className: "row-issues"}, props.issues.filter(issue => issue.series == serie.id).map((issue, index) => h("span", {key: index, className: issue.level}, issue.message)))
    )
  );
}

function SeasonPanel(props) {
  const summary = h("button", {className: "season-summary", onClick: props.onToggle}, h(Icon, null, props.expanded ? "expand_more" : "chevron_right"), "Season " + props.season.season, h(Badge, null, "URLs: " + props.season.urls.filter(Boolean).length));
  if (!props.expanded) return h("section", {className: "season-panel"}, summary);
  const actions = h("div", {className: "season-actions"},
    h("input", {disabled: props.absolute, value: props.season.season, onChange: e => props.onChange({season: e.target.value})}),
    h("button", {className: "btn text", onClick: props.onAddUrl}, "Add URL"),
    h("button", {className: "btn text", onClick: props.onSortUrls}, "Sort part URLs"),
    h("button", {className: "btn text", onClick: props.onSaveOrder}, "Save order"),
    h("button", {className: "btn text", onClick: props.onResetOrder}, "Reset unsaved order"),
    h("button", {className: "btn text", onClick: props.onPreviewOrder}, "Preview episode order"),
    h("button", {className: "btn text danger", onClick: props.onRemove}, "Remove season")
  );
  const urlRows = props.season.urls.map((url, index) => h("div", {className: "url-row", key: index},
    h("span", {className: "url-order"}, "#" + (index + 1)),
    h("button", {className: "btn icon", disabled: index == 0, title: "Move up", onClick: () => props.onMoveUrl(index, -1)}, h(Icon, null, "arrow_upward")),
    h("button", {className: "btn icon", disabled: index == props.season.urls.length - 1, title: "Move down", onClick: () => props.onMoveUrl(index, 1)}, h(Icon, null, "arrow_downward")),
    h("input", {value: url, placeholder: "https://...", onChange: e => props.onUrlChange(index, e.target.value)}),
    h("button", {className: "btn icon", disabled: !url, title: "Copy URL", onClick: () => navigator.clipboard.writeText(url)}, h(Icon, null, "content_copy")),
    url && h("a", {className: "btn icon", title: "Open URL", href: url, target: "_blank", rel: "noopener"}, h(Icon, null, "open_in_new")),
    h("button", {className: "btn icon", title: "Remove URL", onClick: () => props.onRemoveUrl(index)}, h(Icon, null, "delete"))
  ));
  const preview = props.preview && h("div", {className: "flatten-preview"},
    h("h4", null, "Route preview"),
    props.preview.urls.map(item => h("div", {key: item.order, className: "preview-row"}, h("strong", null, "#" + item.order + " " + (item.source_title || item.url)), h("span", null, item.source_episode_count ? "source E1-E" + item.source_episode_count : "episode count unavailable"), h("span", null, item.flattened_start ? "flattened S" + props.season.season + "E" + item.flattened_start + "-E" + item.flattened_end : "flatten range unavailable"))),
    (props.preview.warnings || []).map((warning, index) => h("p", {key: index, className: "warning"}, warning))
  );
  return h("section", {className: "season-panel"}, summary, h("div", {className: "season-body"}, actions, urlRows, preview));
}

function SearchResults({result, seasons, onPick, onAlias}) {
  if (result.status == "already_mapped") return h("div", {className: "search-results"}, h(Badge, {tone: "ok"}, "already mapped"), h("span", null, "Search V3 skipped because this season already has a valid URL."));
  return h("div", {className: "search-results"}, h("div", {className: "surface-title"}, h(Badge, {tone: result.status == "matched" ? "ok" : "warning"}, result.status.replaceAll("_", " ")), h("span", null, "Index " + result.index.size + " entries")),
    (result.top_candidates || []).slice(0, 8).map((match, i) => h(Candidate, {key: i, match, seasons, onPick, onAlias})),
    !(result.top_candidates || []).length && h("p", null, "No result. Debug details are available in the log panel."));
}

function Candidate({match, seasons, onPick, onAlias}) {
  const [season, setSeason] = React.useState(0);
  return h("div", {className: "candidate"}, h("strong", null, match.title), h("span", null, match.audio + " | " + Number(match.score).toFixed(2) + " | " + match.reason), h("a", {href: match.url, target: "_blank", rel: "noopener"}, match.url), h("select", {value: season, onChange: e => setSeason(e.target.value)}, seasons.map((item, i) => h("option", {key: i, value: i}, "Season " + item.season))), h("button", {className: "btn text primary", onClick: () => onPick(match, season)}, "Use candidate"), h("button", {className: "btn text", onClick: () => onAlias(match)}, "Add alias"));
}

function ParserReport({result}) {
  const total = Math.max(1, result.scanned_seasons || 0);
  const bars = [["Already mapped", result.skipped_search || 0, "ok"], ["Skipped TBA", result.skipped_future_tba || 0, "ok"], ["New mappings", result.new_mappings_saved || 0, "saved"], ["Needs review", result.needs_review || 0, "warning"], ["Errors", result.errors_count || 0, "error"]];
  return h("section", {className: "surface parser-report"},
    h("div", {className: "summary-grid compact-grid"}, [["Scanned series", result.scanned_series], ["Scanned seasons", result.scanned_seasons], ["Skipped mapped", result.skipped_search], ["Skipped TBA", result.skipped_future_tba], ["New saved", result.new_mappings_saved], ["Needs review", result.needs_review], ["Duplicate URLs", result.duplicate_url_warnings], ["No result", result.no_result], ["Errors", result.errors_count]].map(value => h(SummaryCard, {key: value[0], title: value[0], value: value[1] || 0}))),
    h("div", {className: "bars"}, bars.map(bar => h("div", {className: "bar-row", key: bar[0]}, h("span", null, bar[0]), h("div", {className: "bar-track"}, h("i", {className: bar[2], style: {width: Math.round(bar[1] * 100 / total) + "%"}})), h("small", null, Math.round(bar[1] * 100 / total) + "%")))),
    h("div", {className: "table-wrap"}, h("table", {className: "report-table"}, h("thead", null, h("tr", null, ["Sonarr title", "Season", "Sonarr season status", "Released episodes", "Future/TBA?", "Existing mapping", "Search V3 used?", "Candidate", "Score", "Audio", "Action", "Notes"].map(label => h("th", {key: label}, label)))), h("tbody", null, (result.items || []).slice().sort((a, b) => a.title.localeCompare(b.title) || Number(a.season) - Number(b.season)).map((item, i) => h("tr", {key: i}, h("td", null, item.title), h("td", null, "S" + item.season), h("td", null, h(Badge, {tone: item.status == "skipped_future_tba" ? "warning" : ""}, item.sonarr_season_status || "unknown")), h("td", null, item.released_episodes == null ? "-" : item.released_episodes), h("td", null, item.future_tba ? "yes" : "no"), h("td", null, item.existing_mapping ? "yes" : "no"), h("td", null, item.search_v3_used ? "yes" : "no"), h("td", null, item.selected_candidate || "-"), h("td", null, item.score == null ? "-" : Number(item.score).toFixed(2)), h("td", null, item.language || "-"), h("td", null, item.action || item.action_taken || "-"), h("td", null, item.notes || item.reason || "-")))))));
}

function EventRows({events, verbosity}) {
  return h("div", {className: "event-list"}, (events || []).map(event => h("details", {key: event.id, className: "event-row " + event.status, open: verbosity == "DEBUG"}, h("summary", null, h("time", null, new Date(event.timestamp).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})), h(Badge, {tone: event.status == "success" ? "ok" : event.status == "warning" ? "warning" : event.status == "error" ? "error" : ""}, event.type), h("span", null, event.compact)), h("pre", null, JSON.stringify(event.details || {}, null, 2)))));
}

function LogPanel(props) {
  const filters = [["all", "All"], ["SEARCH_V3", "Search"], ["MAPPING", "Mapping"], ["SONARR", "Sonarr"], ["DOWNLOAD", "Download"], ["error", "Errors"], ["warning", "Needs review"]];
  const events = props.events.filter(event => {
    if (props.filter != "all" && event.type != props.filter && event.status != props.filter) return false;
    return !props.search || JSON.stringify(event).toLowerCase().includes(props.search.toLowerCase());
  });
  return h("section", {className: "log-panel " + (props.full ? "full" : "")},
    h("div", {className: "log-header"}, h("h2", null, "Live activity"), h(Badge, null, events.length + " events")),
    h("div", {className: "log-tools"}, h("input", {type: "search", placeholder: "Search logs", value: props.search, onChange: e => props.onSearch(e.target.value)}), h("select", {value: props.verbosity, title: "Log verbosity", onChange: e => props.onVerbosity(e.target.value)}, ["COMPACT", "NORMAL", "DEBUG", "RAW"].map(value => h("option", {key: value, value}, value))), h("button", {className: "btn icon", title: props.paused ? "Resume log view" : "Pause log view", onClick: () => props.onPause(!props.paused)}, h(Icon, null, props.paused ? "play_arrow" : "pause")), h("button", {className: "btn icon", title: "Refresh logs", onClick: props.onRefresh}, h(Icon, null, "refresh"))),
    h("div", {className: "filter-chips"}, filters.map(filter => h("button", {key: filter[0], className: props.filter == filter[0] ? "chip active" : "chip", onClick: () => props.onFilter(filter[0])}, filter[1]))),
    props.verbosity == "RAW" ? h("pre", {className: "raw-log"}, props.raw || "Load raw logs with refresh.") : h(EventRows, {events, verbosity: props.verbosity}),
    h("div", {className: "log-footer"}, h("label", {className: "switch"}, h("input", {type: "checkbox", checked: props.autoScroll, onChange: e => props.onAutoScroll(e.target.checked)}), "Auto-scroll"), h("button", {className: "btn text", onClick: props.onCopy}, "Copy visible"), h("button", {className: "btn text", onClick: props.onCopyRaw}, "Copy raw"), h("a", {className: "btn text", href: "/ie/log"}, "Download"), h("button", {className: "btn text", onClick: props.onClear}, "Clear visible"))
  );
}

const container = document.querySelector("#root");
ReactDOM.render(h(AniDownDashboard), container);

const loginView = document.querySelector("#loginView");
const journalView = document.querySelector("#journalView");
const loginForm = document.querySelector("#loginForm");
const loginError = document.querySelector("#loginError");
const todayTitle = document.querySelector("#todayTitle");
const todayEditor = document.querySelector("#todayEditor");
const saveState = document.querySelector("#saveState");
const olderNotes = document.querySelector("#olderNotes");
const logoutButton = document.querySelector("#logoutButton");
const settingsButton = document.querySelector("#settingsButton");
const personalMode = document.querySelector("#personalMode");
const workMode = document.querySelector("#workMode");
const contextLabel = document.querySelector("#contextLabel");
const toolbar = document.querySelector(".editor-toolbar");
const collapseButton = document.querySelector("#collapseButton");
const imageButton = document.querySelector("#imageButton");
const imageInput = document.querySelector("#imageInput");
const soundToggle = document.querySelector("#soundToggle");
const todayTags = document.querySelector("#todayTags");
const searchForm = document.querySelector("#searchForm");
const searchInput = document.querySelector("#searchInput");
const jumpForm = document.querySelector("#jumpForm");
const jumpDate = document.querySelector("#jumpDate");
const exportForm = document.querySelector("#exportForm");
const exportFrom = document.querySelector("#exportFrom");
const exportTo = document.querySelector("#exportTo");
const exportFormat = document.querySelector("#exportFormat");
const resultsPanel = document.querySelector("#resultsPanel");
const resultsTitle = document.querySelector("#resultsTitle");
const resultsList = document.querySelector("#resultsList");
const clearResults = document.querySelector("#clearResults");
const passwordDialog = document.querySelector("#passwordDialog");
const passwordForm = document.querySelector("#passwordForm");
const passwordError = document.querySelector("#passwordError");
const cancelPassword = document.querySelector("#cancelPassword");
const downloadBackup = document.querySelector("#downloadBackup");
const serverBackup = document.querySelector("#serverBackup");
const restoreBackupButton = document.querySelector("#restoreBackupButton");
const restoreBackupInput = document.querySelector("#restoreBackupInput");
const backupStatus = document.querySelector("#backupStatus");
const importContext = document.querySelector("#importContext");
const importDateOrder = document.querySelector("#importDateOrder");
const importText = document.querySelector("#importText");
const previewImport = document.querySelector("#previewImport");
const commitImport = document.querySelector("#commitImport");
const importStatus = document.querySelector("#importStatus");
const importReview = document.querySelector("#importReview");
const pinDialog = document.querySelector("#pinDialog");
const pinForm = document.querySelector("#pinForm");
const pinTitle = document.querySelector("#pinTitle");
const personalPin = document.querySelector("#personalPin");
const pinError = document.querySelector("#pinError");
const pinSubmit = document.querySelector("#pinSubmit");
const useWorkInstead = document.querySelector("#useWorkInstead");

let saveTimer = null;
let lastSavedContent = "";
let saving = false;
let activeContext = localStorage.getItem("journalContext") || "personal";
let soundMuted = localStorage.getItem("typewriterMuted") === "true";
let audioContext = null;
let keySoundBuffer = null;
let keySoundPromise = null;
let lastKeySoundAt = 0;
let personalToken = "";
let pinMode = "unlock";
let personalIdleTimer = null;
let lastPersonalActivityAt = 0;
let importEntries = [];
const PERSONAL_IDLE_MS = 30 * 60 * 1000;
const SOUND_KEYS = new Set([
  "Backspace",
  "Enter",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "PageUp",
  "PageDown",
  "Home",
  "End",
]);
const KEY_SOUND_URL = "/static/typewriter-key.mp3?v=1";
const KEY_SOUND_GAIN = 3.5;

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (personalToken) {
    headers["X-Personal-Token"] = personalToken;
  }
  const response = await fetch(path, {
    headers,
    credentials: "same-origin",
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || "Request failed");
    error.pinRequired = Boolean(data.pinRequired);
    throw error;
  }
  return data;
}

function htmlFromPlainText(text) {
  if (!text) return "";
  const wrapper = document.createElement("div");
  wrapper.textContent = text;
  return `<p>${wrapper.innerHTML.replace(/\n/g, "<br>")}</p>`;
}

function normalizeStoredContent(content) {
  return /<\/?[a-z][\s\S]*>/i.test(content) ? content : htmlFromPlainText(content);
}

function formatDate(value) {
  const date = new Date(`${value}T12:00:00`);
  return new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

function showLogin() {
  loginView.hidden = false;
  journalView.hidden = true;
  document.querySelector("#username").focus();
}

function showJournal() {
  loginView.hidden = true;
  journalView.hidden = false;
}

function setTheme(context) {
  activeContext = context === "work" ? "work" : "personal";
  localStorage.setItem("journalContext", activeContext);
  document.body.dataset.context = activeContext;
  personalMode.classList.toggle("active", activeContext === "personal");
  workMode.classList.toggle("active", activeContext === "work");
  contextLabel.textContent = activeContext === "work" ? "Work Journal" : "Personal Journal";
}

function openPinDialog(mode) {
  pinMode = mode;
  pinTitle.textContent = mode === "set" ? "Set Personal PIN" : "Enter Personal PIN";
  pinSubmit.textContent = mode === "set" ? "Set PIN" : "Unlock";
  pinError.textContent = "";
  personalPin.value = "";
  if (!pinDialog.open) pinDialog.showModal();
  requestAnimationFrame(() => personalPin.focus());
}

function clearPersonalIdleTimer() {
  clearTimeout(personalIdleTimer);
  personalIdleTimer = null;
}

function personalIsIdle() {
  return lastPersonalActivityAt && Date.now() - lastPersonalActivityAt >= PERSONAL_IDLE_MS;
}

async function lockPersonalSpace({ save = true } = {}) {
  if (!personalToken) return;
  if (save) await saveToday();
  personalToken = "";
  clearPersonalIdleTimer();
  if (activeContext === "personal") {
    await loadJournal();
  }
}

function resetPersonalIdleTimer() {
  if (activeContext !== "personal" || !personalToken || pinDialog.open) return;
  lastPersonalActivityAt = Date.now();
  clearTimeout(personalIdleTimer);
  personalIdleTimer = setTimeout(lockPersonalSpace, PERSONAL_IDLE_MS);
}

function checkPersonalIdle() {
  if (activeContext !== "personal" || !personalToken) return;
  if (personalIsIdle()) {
    lockPersonalSpace();
    return;
  }
  resetPersonalIdleTimer();
}

async function ensurePersonalUnlocked() {
  if (activeContext !== "personal" || personalToken) return true;
  todayEditor.innerHTML = "";
  todayTags.value = "";
  olderNotes.textContent = "";
  saveState.textContent = "Locked";
  showJournal();
  return requestPersonalPinAccess();
}

async function requestPersonalPinAccess() {
  if (personalToken) return true;
  const status = await api("/api/personal-pin");
  openPinDialog(status.isSet ? "unlock" : "set");
  return false;
}

function setSoundMuted(muted) {
  soundMuted = Boolean(muted);
  localStorage.setItem("typewriterMuted", String(soundMuted));
  soundToggle.checked = !soundMuted;
}

async function playTypewriterSound(key) {
  if (soundMuted || !key || (key.length !== 1 && !SOUND_KEYS.has(key))) return;
  const now = performance.now();
  if (now - lastKeySoundAt < 30) return;
  lastKeySoundAt = now;
  const AudioEngine = window.AudioContext || window.webkitAudioContext;
  if (!AudioEngine) return;
  audioContext = audioContext || new AudioEngine();
  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }
  const buffer = await loadKeySound();
  const source = audioContext.createBufferSource();
  const gain = audioContext.createGain();
  source.buffer = buffer;
  gain.gain.value = KEY_SOUND_GAIN;
  source.connect(gain);
  gain.connect(audioContext.destination);
  source.start();
}

async function loadKeySound() {
  if (keySoundBuffer) return keySoundBuffer;
  if (!keySoundPromise) {
    keySoundPromise = fetch(KEY_SOUND_URL)
      .then((response) => {
        if (!response.ok) throw new Error("Keyboard sound failed to load");
        return response.arrayBuffer();
      })
      .then((data) => audioContext.decodeAudioData(data));
  }
  keySoundBuffer = await keySoundPromise;
  return keySoundBuffer;
}

function renderOlder(notes) {
  olderNotes.textContent = "";
  if (!notes.length) {
    const empty = document.createElement("p");
    empty.className = "empty-history";
    empty.textContent = "Older notes will appear here after future writing days.";
    olderNotes.append(empty);
    return;
  }

  for (const note of notes) {
    if (note.collapsed) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "collapsed-note";
      button.dataset.date = note.note_date;
      button.dataset.context = note.context || activeContext;
      const date = document.createElement("span");
      date.textContent = formatDate(note.note_date);
      const count = document.createElement("span");
      const words = Number(note.word_count || 0);
      count.textContent = `${words} ${words === 1 ? "word" : "words"}`;
      button.append(date, count);
      button.addEventListener("click", () => expandCollapsedNote(button));
      olderNotes.append(button);
      continue;
    }
    const article = document.createElement("article");
    article.className = "note-card";
    const time = document.createElement("time");
    time.dateTime = note.note_date;
    time.textContent = formatDate(note.note_date);
    const tags = renderTags(note.tags || "");
    const content = document.createElement("div");
    content.className = "note-content";
    content.innerHTML = normalizeStoredContent(note.content || "");
    article.append(time);
    if (tags) article.append(tags);
    article.append(content);
    olderNotes.append(article);
  }
}

async function expandCollapsedNote(button) {
  button.disabled = true;
  const noteDate = button.dataset.date;
  const context = button.dataset.context || activeContext;
  try {
    const data = await api(`/api/note?date=${encodeURIComponent(noteDate)}&context=${encodeURIComponent(context)}`);
    const note = data.note;
    const article = document.createElement("article");
    article.className = "note-card";
    const time = document.createElement("time");
    time.dateTime = note.note_date;
    time.textContent = formatDate(note.note_date);
    const tags = renderTags(note.tags || "");
    const content = document.createElement("div");
    content.className = "note-content";
    content.innerHTML = normalizeStoredContent(note.content || "");
    article.append(time);
    if (tags) article.append(tags);
    article.append(content);
    button.replaceWith(article);
  } catch (error) {
    button.disabled = false;
    button.querySelector("span:last-child").textContent = error.message;
  }
}

function renderTags(tags) {
  const items = tags.split(",").map((tag) => tag.trim()).filter(Boolean);
  if (!items.length) return null;
  const wrap = document.createElement("div");
  wrap.className = "tag-list";
  for (const tag of items) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "tag-chip";
    chip.textContent = tag;
    chip.addEventListener("click", () => searchByTag(tag));
    wrap.append(chip);
  }
  return wrap;
}

function renderResults(title, results) {
  resultsPanel.hidden = false;
  resultsTitle.textContent = title;
  resultsList.textContent = "";
  if (!results.length) {
    const empty = document.createElement("p");
    empty.className = "empty-history";
    empty.textContent = "No matching notes.";
    resultsList.append(empty);
    return;
  }
  for (const result of results) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "result-row";
    button.addEventListener("click", () => showSelectedNote(result.note_date, result.context || activeContext));
    const date = document.createElement("strong");
    date.textContent = formatDate(result.note_date);
    const snippet = document.createElement("span");
    snippet.textContent = result.snippet || result.tags || "No preview";
    button.append(date, snippet);
    resultsList.append(button);
  }
  resultsPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderImportReview(entries) {
  importEntries = entries.map((entry) => ({ ...entry }));
  importReview.textContent = "";
  importReview.hidden = !importEntries.length;
  commitImport.disabled = !importEntries.some((entry) => entry.date && entry.text && entry.action !== "skip");
  for (const entry of importEntries) {
    const row = document.createElement("article");
    row.className = "import-row";
    const meta = document.createElement("div");
    meta.className = "import-meta";
    const date = document.createElement("input");
    date.type = "date";
    date.value = entry.date || "";
    date.addEventListener("input", () => {
      entry.date = date.value;
      commitImport.disabled = !importEntries.some((item) => item.date && item.text && item.action !== "skip");
    });
    const context = document.createElement("select");
    context.innerHTML = '<option value="personal">Personal</option><option value="work">Work</option>';
    context.value = entry.context || importContext.value;
    context.addEventListener("change", () => {
      entry.context = context.value;
    });
    const action = document.createElement("select");
    action.innerHTML = '<option value="append">Append</option><option value="replace">Replace</option><option value="skip">Skip</option>';
    action.value = entry.action || "skip";
    action.addEventListener("change", () => {
      entry.action = action.value;
      commitImport.disabled = !importEntries.some((item) => item.date && item.text && item.action !== "skip");
    });
    meta.append(date, context, action);
    const text = document.createElement("textarea");
    text.rows = 4;
    text.value = entry.text || "";
    text.addEventListener("input", () => {
      entry.text = text.value;
      commitImport.disabled = !importEntries.some((item) => item.date && item.text && item.action !== "skip");
    });
    const note = document.createElement("p");
    note.className = "import-warning";
    const parts = [];
    if (entry.rawDate) parts.push(`Read "${entry.rawDate}" as ${entry.date || "unknown date"}.`);
    if (entry.hasExisting) parts.push("Existing note found.");
    if (entry.warning) parts.push(entry.warning);
    note.textContent = parts.join(" ");
    row.append(meta, text, note);
    importReview.append(row);
  }
}

async function showSelectedNote(date, context = activeContext) {
  const data = await api(`/api/note?date=${encodeURIComponent(date)}&context=${encodeURIComponent(context)}`);
  const note = data.note;
  resultsPanel.hidden = false;
  resultsTitle.textContent = formatDate(note.note_date);
  resultsList.textContent = "";
  const article = document.createElement("article");
  article.className = "note-card selected-note";
  const tags = renderTags(note.tags || "");
  const content = document.createElement("div");
  content.className = "note-content";
  content.innerHTML = normalizeStoredContent(note.content || "");
  if (tags) article.append(tags);
  if (note.content) {
    article.append(content);
  } else {
    const empty = document.createElement("p");
    empty.className = "empty-history";
    empty.textContent = "No note exists for this date.";
    article.append(empty);
  }
  resultsList.append(article);
  resultsPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function placeCursorAtEnd() {
  todayEditor.focus();
  const range = document.createRange();
  range.selectNodeContents(todayEditor);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

async function loadJournal() {
  setTheme(activeContext);
  if (!(await ensurePersonalUnlocked())) return;
  let data;
  try {
    data = await api(`/api/journal?context=${encodeURIComponent(activeContext)}`);
  } catch (error) {
    if (error.pinRequired) {
      await lockPersonalSpace({ save: false });
      return;
    }
    throw error;
  }
  todayTitle.textContent = formatDate(data.today.date);
  todayEditor.innerHTML = normalizeStoredContent(data.today.content || "");
  todayTags.value = data.today.tags || "";
  lastSavedContent = todayEditor.innerHTML;
  todayTags.dataset.saved = todayTags.value;
  saveState.textContent = data.today.updatedAt ? "Saved" : "New";
  renderOlder(data.older || []);
  showJournal();
  resetPersonalIdleTimer();
  requestAnimationFrame(placeCursorAtEnd);
}

async function saveToday() {
  if (activeContext === "personal" && !personalToken) return;
  const currentContent = todayEditor.innerHTML;
  const currentTags = todayTags.value;
  if (saving || (currentContent === lastSavedContent && currentTags === todayTags.dataset.saved)) return;
  saving = true;
  saveState.textContent = "Saving...";
  try {
    await api("/api/journal/today", {
      method: "POST",
      body: JSON.stringify({ content: currentContent, tags: currentTags, context: activeContext }),
    });
    lastSavedContent = currentContent;
    todayTags.dataset.saved = currentTags;
    saveState.textContent = "Saved";
  } catch (error) {
    if (error.pinRequired) {
      await lockPersonalSpace({ save: false });
      return;
    }
    saveState.textContent = "Save failed";
  } finally {
    saving = false;
  }
}

function exec(command) {
  todayEditor.focus();
  document.execCommand(command, false, null);
  scheduleSave();
}

function insertHtmlAtCursor(markup) {
  todayEditor.focus();
  document.execCommand("insertHTML", false, markup);
  scheduleSave();
}

function selectionInsideEditor(selection) {
  if (!selection || selection.rangeCount === 0) return false;
  const range = selection.getRangeAt(0);
  return todayEditor.contains(range.commonAncestorContainer);
}

function titleFromSelection(text) {
  const words = text.trim().split(/\s+/).filter(Boolean).slice(0, 8);
  return words.join(" ") || "Collapsed section";
}

function collapseSelectedSection() {
  todayEditor.focus();
  const selection = window.getSelection();
  if (!selectionInsideEditor(selection) || selection.isCollapsed) {
    saveState.textContent = "Select text to collapse";
    return;
  }
  const range = selection.getRangeAt(0);
  const selectedText = selection.toString();
  const title = window.prompt("Section title", titleFromSelection(selectedText));
  if (title === null) return;
  const fragment = range.extractContents();
  const details = document.createElement("details");
  details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = title.trim() || titleFromSelection(selectedText);
  const body = document.createElement("div");
  if ([...fragment.childNodes].some((node) => node.nodeType === Node.ELEMENT_NODE)) {
    body.append(fragment);
  } else {
    const paragraph = document.createElement("p");
    paragraph.append(fragment);
    body.append(paragraph);
  }
  details.append(summary, body);
  range.insertNode(details);
  selection.removeAllRanges();
  const nextRange = document.createRange();
  nextRange.selectNodeContents(body);
  nextRange.collapse(false);
  selection.addRange(nextRange);
  scheduleSave();
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function uploadRestore(file) {
  if (!file) return;
  backupStatus.textContent = "Restoring backup...";
  const dataUrl = await readFileAsDataUrl(file);
  const result = await api("/api/restore", {
    method: "POST",
    body: JSON.stringify({ dataUrl }),
  });
  backupStatus.textContent = `Restored ${result.notes} notes and ${result.assets} images.`;
  await loadJournal();
}

async function uploadImage(file) {
  if (!file.type.startsWith("image/")) return;
  if (file.size > 8_000_000) {
    saveState.textContent = "Image too large";
    return;
  }
  saveState.textContent = "Uploading image...";
  const dataUrl = await readFileAsDataUrl(file);
  const asset = await api("/api/assets", {
    method: "POST",
    body: JSON.stringify({ filename: file.name, type: file.type, dataUrl }),
  });
  const alt = file.name ? file.name.replace(/[<>"']/g, "") : "Journal image";
  insertHtmlAtCursor(`<img src="${asset.url}" alt="${alt}" loading="lazy">`);
}

function scheduleSave() {
  saveState.textContent = "Unsaved";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveToday, 700);
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  const username = document.querySelector("#username").value;
  const password = document.querySelector("#password").value;
  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    await loadJournal();
  } catch (error) {
    loginError.textContent = error.message;
  }
});

todayEditor.addEventListener("input", scheduleSave);
todayEditor.addEventListener("blur", saveToday);
todayEditor.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  playTypewriterSound(event.key).catch(() => {});
});
todayTags.addEventListener("input", scheduleSave);
todayTags.addEventListener("blur", saveToday);

soundToggle.addEventListener("change", () => {
  setSoundMuted(!soundToggle.checked);
  if (!soundMuted) playTypewriterSound(" ").catch(() => {});
});

toolbar.addEventListener("click", (event) => {
  const button = event.target.closest("[data-command]");
  if (!button) return;
  exec(button.dataset.command);
});

collapseButton.addEventListener("click", collapseSelectedSection);

async function searchByTag(tag) {
  const data = await api(`/api/search?tag=${encodeURIComponent(tag)}&context=${encodeURIComponent(activeContext)}`);
  renderResults(`${activeContext === "work" ? "Work" : "Personal"} tag: ${tag}`, data.results || []);
}

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = searchInput.value.trim();
  if (query.length < 2) return;
  const data = await api(`/api/search?q=${encodeURIComponent(query)}&context=${encodeURIComponent(activeContext)}`);
  renderResults(`${activeContext === "work" ? "Work" : "Personal"} search: ${query}`, data.results || []);
});

jumpForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!jumpDate.value) return;
  await showSelectedNote(jumpDate.value, activeContext);
});

exportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (activeContext === "personal" && !(await requestPersonalPinAccess())) return;
  const params = new URLSearchParams();
  if (exportFrom.value) params.set("from", exportFrom.value);
  if (exportTo.value) params.set("to", exportTo.value);
  params.set("format", exportFormat.value);
  params.set("context", activeContext);
  if (activeContext === "personal") params.set("personalToken", personalToken);
  window.location.href = `/api/export?${params.toString()}`;
});

clearResults.addEventListener("click", () => {
  resultsPanel.hidden = true;
  resultsList.textContent = "";
});

document.querySelectorAll(".context-option").forEach((button) => {
  button.addEventListener("click", async () => {
    if (button.dataset.context === activeContext) return;
    await saveToday();
    personalToken = "";
    clearPersonalIdleTimer();
    activeContext = button.dataset.context;
    resultsPanel.hidden = true;
    resultsList.textContent = "";
    await loadJournal();
  });
});

imageButton.addEventListener("click", () => imageInput.click());

imageInput.addEventListener("change", async () => {
  const file = imageInput.files[0];
  imageInput.value = "";
  if (!file) return;
  try {
    await uploadImage(file);
  } catch {
    saveState.textContent = "Image upload failed";
  }
});

todayEditor.addEventListener("paste", async (event) => {
  const images = [...event.clipboardData.items]
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (!images.length) return;
  event.preventDefault();
  for (const image of images) {
    try {
      await uploadImage(image);
    } catch {
      saveState.textContent = "Image upload failed";
    }
  }
});

logoutButton.addEventListener("click", async () => {
  await saveToday();
  personalToken = "";
  clearPersonalIdleTimer();
  await api("/api/logout", { method: "POST" });
  showLogin();
});

settingsButton.addEventListener("click", () => {
  passwordError.textContent = "";
  backupStatus.textContent = "";
  importStatus.textContent = "";
  importContext.value = activeContext;
  passwordDialog.showModal();
});

cancelPassword.addEventListener("click", () => passwordDialog.close());

passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  passwordError.textContent = "";
  try {
    await api("/api/password", {
      method: "POST",
      body: JSON.stringify({
        currentPassword: document.querySelector("#currentPassword").value,
        newPassword: document.querySelector("#newPassword").value,
      }),
    });
    passwordDialog.close();
    personalToken = "";
    clearPersonalIdleTimer();
    await api("/api/logout", { method: "POST" });
    showLogin();
  } catch (error) {
    passwordError.textContent = error.message;
  }
});

downloadBackup.addEventListener("click", async () => {
  if (!(await requestPersonalPinAccess())) return;
  window.location.href = `/api/backup?personalToken=${encodeURIComponent(personalToken)}`;
});

serverBackup.addEventListener("click", async () => {
  backupStatus.textContent = "Creating server backup...";
  try {
    if (!(await requestPersonalPinAccess())) return;
    const result = await api("/api/backups", { method: "POST" });
    backupStatus.textContent = `Saved ${result.name}.`;
  } catch (error) {
    backupStatus.textContent = error.message;
  }
});

restoreBackupButton.addEventListener("click", () => restoreBackupInput.click());

restoreBackupInput.addEventListener("change", async () => {
  const file = restoreBackupInput.files[0];
  restoreBackupInput.value = "";
  try {
    if (!(await requestPersonalPinAccess())) return;
    await uploadRestore(file);
  } catch (error) {
    backupStatus.textContent = error.message;
  }
});

previewImport.addEventListener("click", async () => {
  importStatus.textContent = "Parsing notes...";
  importReview.hidden = true;
  importReview.textContent = "";
  commitImport.disabled = true;
  try {
    if (importContext.value === "personal" && !(await requestPersonalPinAccess())) {
      importStatus.textContent = "Unlock Personal to preview import.";
      return;
    }
    const result = await api("/api/import/preview", {
      method: "POST",
      body: JSON.stringify({
        text: importText.value,
        context: importContext.value,
        dateOrder: importDateOrder.value,
      }),
    });
    renderImportReview(result.entries || []);
    importStatus.textContent = `Found ${result.importable || 0} importable notes. ${result.warnings || 0} warnings.`;
  } catch (error) {
    importStatus.textContent = error.message;
  }
});

commitImport.addEventListener("click", async () => {
  importStatus.textContent = "Importing notes...";
  commitImport.disabled = true;
  try {
    if (importEntries.some((entry) => entry.context === "personal" && entry.action !== "skip") && !(await requestPersonalPinAccess())) {
      importStatus.textContent = "Unlock Personal to import.";
      commitImport.disabled = false;
      return;
    }
    const result = await api("/api/import/commit", {
      method: "POST",
      body: JSON.stringify({ entries: importEntries }),
    });
    importStatus.textContent = `Imported ${result.imported} notes. Appended ${result.appended}, replaced ${result.replaced}, skipped ${result.skipped}. Backup: ${result.backup || "not needed"}.`;
    importReview.hidden = true;
    importReview.textContent = "";
    importEntries = [];
    importText.value = "";
    await loadJournal();
  } catch (error) {
    importStatus.textContent = error.message;
    commitImport.disabled = false;
  }
});

pinForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const pin = personalPin.value.trim();
  if (!/^\d{4}$/.test(pin)) {
    pinError.textContent = "Enter exactly 4 digits.";
    return;
  }
  try {
    const result = await api(pinMode === "set" ? "/api/personal-pin" : "/api/personal-pin/unlock", {
      method: "POST",
      body: JSON.stringify({ pin }),
    });
    personalToken = result.token;
    pinDialog.close();
    resetPersonalIdleTimer();
    await loadJournal();
  } catch (error) {
    pinError.textContent = error.message;
  }
});

personalPin.addEventListener("input", () => {
  personalPin.value = personalPin.value.replace(/\D/g, "").slice(0, 4);
});

useWorkInstead.addEventListener("click", async () => {
  personalToken = "";
  clearPersonalIdleTimer();
  activeContext = "work";
  pinDialog.close();
  await loadJournal();
});

["pointerdown", "keydown", "scroll", "touchstart"].forEach((eventName) => {
  document.addEventListener(eventName, resetPersonalIdleTimer, { passive: true });
});

window.addEventListener("focus", checkPersonalIdle);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) checkPersonalIdle();
});

(async function boot() {
  setTheme(activeContext);
  setSoundMuted(soundMuted);
  try {
    const me = await api("/api/me");
    if (me.authenticated) {
      await loadJournal();
    } else {
      showLogin();
    }
  } catch {
    showLogin();
  }
})();

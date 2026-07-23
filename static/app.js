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

let saveTimer = null;
let lastSavedContent = "";
let saving = false;
let activeContext = localStorage.getItem("journalContext") || "personal";
let soundMuted = localStorage.getItem("typewriterMuted") === "true";
let audioContext = null;
let lastKeySoundAt = 0;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "same-origin",
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Request failed");
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

function setSoundMuted(muted) {
  soundMuted = Boolean(muted);
  localStorage.setItem("typewriterMuted", String(soundMuted));
  soundToggle.checked = !soundMuted;
}

function playTypewriterSound(key) {
  if (soundMuted || !key || key.length !== 1) return;
  const now = performance.now();
  if (now - lastKeySoundAt < 24) return;
  lastKeySoundAt = now;
  audioContext = audioContext || new AudioContext();
  const duration = 0.035;
  const start = audioContext.currentTime;
  const oscillator = audioContext.createOscillator();
  const gain = audioContext.createGain();
  const filter = audioContext.createBiquadFilter();
  oscillator.type = "square";
  oscillator.frequency.value = 130 + Math.random() * 80;
  filter.type = "lowpass";
  filter.frequency.value = 850;
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(0.045, start + 0.004);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  oscillator.connect(filter);
  filter.connect(gain);
  gain.connect(audioContext.destination);
  oscillator.start(start);
  oscillator.stop(start + duration);
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
  const data = await api(`/api/journal?context=${encodeURIComponent(activeContext)}`);
  todayTitle.textContent = formatDate(data.today.date);
  todayEditor.innerHTML = normalizeStoredContent(data.today.content || "");
  todayTags.value = data.today.tags || "";
  lastSavedContent = todayEditor.innerHTML;
  todayTags.dataset.saved = todayTags.value;
  saveState.textContent = data.today.updatedAt ? "Saved" : "New";
  renderOlder(data.older || []);
  showJournal();
  requestAnimationFrame(placeCursorAtEnd);
}

async function saveToday() {
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
  playTypewriterSound(event.key);
});
todayTags.addEventListener("input", scheduleSave);
todayTags.addEventListener("blur", saveToday);

soundToggle.addEventListener("change", () => {
  setSoundMuted(!soundToggle.checked);
});

toolbar.addEventListener("click", (event) => {
  const button = event.target.closest("[data-command]");
  if (!button) return;
  exec(button.dataset.command);
});

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

exportForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const params = new URLSearchParams();
  if (exportFrom.value) params.set("from", exportFrom.value);
  if (exportTo.value) params.set("to", exportTo.value);
  params.set("format", exportFormat.value);
  params.set("context", activeContext);
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
  await api("/api/logout", { method: "POST" });
  showLogin();
});

settingsButton.addEventListener("click", () => {
  passwordError.textContent = "";
  backupStatus.textContent = "";
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
    await api("/api/logout", { method: "POST" });
    showLogin();
  } catch (error) {
    passwordError.textContent = error.message;
  }
});

downloadBackup.addEventListener("click", () => {
  window.location.href = "/api/backup";
});

serverBackup.addEventListener("click", async () => {
  backupStatus.textContent = "Creating server backup...";
  try {
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
    await uploadRestore(file);
  } catch (error) {
    backupStatus.textContent = error.message;
  }
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

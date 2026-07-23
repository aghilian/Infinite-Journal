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
const toolbar = document.querySelector(".editor-toolbar");
const imageButton = document.querySelector("#imageButton");
const imageInput = document.querySelector("#imageInput");
const passwordDialog = document.querySelector("#passwordDialog");
const passwordForm = document.querySelector("#passwordForm");
const passwordError = document.querySelector("#passwordError");
const cancelPassword = document.querySelector("#cancelPassword");

let saveTimer = null;
let lastSavedContent = "";
let saving = false;

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
    const content = document.createElement("div");
    content.className = "note-content";
    content.innerHTML = normalizeStoredContent(note.content || "");
    article.append(time, content);
    olderNotes.append(article);
  }
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
  const data = await api("/api/journal");
  todayTitle.textContent = formatDate(data.today.date);
  todayEditor.innerHTML = normalizeStoredContent(data.today.content || "");
  lastSavedContent = todayEditor.innerHTML;
  saveState.textContent = data.today.updatedAt ? "Saved" : "New";
  renderOlder(data.older || []);
  showJournal();
  requestAnimationFrame(placeCursorAtEnd);
}

async function saveToday() {
  if (saving || todayEditor.innerHTML === lastSavedContent) return;
  saving = true;
  saveState.textContent = "Saving...";
  try {
    await api("/api/journal/today", {
      method: "POST",
      body: JSON.stringify({ content: todayEditor.innerHTML }),
    });
    lastSavedContent = todayEditor.innerHTML;
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

toolbar.addEventListener("click", (event) => {
  const button = event.target.closest("[data-command]");
  if (!button) return;
  exec(button.dataset.command);
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

(async function boot() {
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

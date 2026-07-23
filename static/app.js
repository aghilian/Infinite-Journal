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
    const pre = document.createElement("pre");
    pre.textContent = note.content;
    article.append(time, pre);
    olderNotes.append(article);
  }
}

function placeCursorAtEnd() {
  todayEditor.focus();
  todayEditor.selectionStart = todayEditor.value.length;
  todayEditor.selectionEnd = todayEditor.value.length;
}

async function loadJournal() {
  const data = await api("/api/journal");
  todayTitle.textContent = formatDate(data.today.date);
  todayEditor.value = data.today.content || "";
  lastSavedContent = todayEditor.value;
  saveState.textContent = data.today.updatedAt ? "Saved" : "New";
  renderOlder(data.older || []);
  showJournal();
  requestAnimationFrame(placeCursorAtEnd);
}

async function saveToday() {
  if (saving || todayEditor.value === lastSavedContent) return;
  saving = true;
  saveState.textContent = "Saving...";
  try {
    await api("/api/journal/today", {
      method: "POST",
      body: JSON.stringify({ content: todayEditor.value }),
    });
    lastSavedContent = todayEditor.value;
    saveState.textContent = "Saved";
  } catch (error) {
    saveState.textContent = "Save failed";
  } finally {
    saving = false;
  }
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

# The Journal

Phase 1 private daily journaling app.

## Features

- Login-required single-user journal.
- Clean daily writing sheet.
- Autosave while typing.
- Reopens the same day at the end of today's note.
- Older daily notes render below today, newest first.
- Password change from the app.

## Local Run

```powershell
$env:THEJOURNAL_PASSWORD="change-this-password"
python app.py
```

Open `http://localhost:8000`.

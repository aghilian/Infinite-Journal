# The Journal

Phase 1 private daily journaling app.

## Features

- Login-required single-user journal.
- Clean daily writing sheet.
- Autosave while typing.
- Reopens the same day at the end of today's note.
- Older daily notes render below today, newest first.
- Password change from the app.
- Rich text formatting for bold, italic, underline, and lists.
- Image upload and paste support.
- Search across notes and tags.
- Date jump for opening a specific day.
- HTML and Markdown export.
- Downloadable and server-side zip backups.
- Restore notes and images from a backup zip.
- Paste-import backdated notes with preview, conflict handling, and automatic backup.
- Security headers, session cleanup, and login throttling.
- Personal and Work contexts with distinct themes and filtered note history.
- Personal space requires a 4-digit PIN on each fresh app open, switch from Work, or after 30 minutes idle.

## Local Run

```powershell
$env:THEJOURNAL_PASSWORD="change-this-password"
python app.py
```

Open `http://localhost:8000`.

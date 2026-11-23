# Cue Copilot (Local Ollama Edition)

A 100% local, privacy-first real-time AI meeting & study assistant for Windows 11 with a frosted-glass UI, live audio transcription, screen OCR, Hybrid PDF RAG, and global hotkeys.

---

## ⚡ 1-Line Install (Windows PowerShell)

Open **Windows PowerShell** and paste this single line:

```powershell
irm https://raw.githubusercontent.com/Gnanesh-2007/cue-copilot/main/install.ps1 | iex
```

That's it! It automatically installs Git, Python, Node.js, Ollama (`qwen2.5:3b`), Electron, and puts a **Cue Copilot** shortcut right on your Desktop.

*(Have a 6 GB+ NVIDIA RTX GPU? Run `ollama pull qwen2.5:7b` for an even stronger local brain.)*

---

## ⌨️ Global Hotkeys (Work From Any App)

| Key | Action |
| :--- | :--- |
| **`F6`** | Toggle Auto / Manual mode |
| **`F7`** | Force Answer (last 60 seconds of call audio) |
| **`F8`** | Instant Hide / Show |
| **`F9`** | Screen OCR / Vision |
| **`F10`** | Toggle Click-Through mode |
| **`F11`** | Teleprompter Auto-Scroll |
| **`Alt + ↑ / ↓`** | Scroll answer up/down without moving your mouse |

---

## 📚 Features
* **Frosted-Glass Windows 11 UI:** Acrylic glass, auto-expanding height, mini-pill Hide mode, syntax-highlighted code blocks with Copy button.
* **Dual-Side Audio + Auto-Reconnect:** Captures meeting audio + your mic (with echo guard) and automatically reconnects when Bluetooth earbuds switch profiles.
* **Hybrid PDF Knowledge Base:** Upload PDFs/DOCX/notes in the Setup tab — shows a live `📄 Found in:` badge whenever your docs match.
* **100% Local & Free:** Runs on local Ollama (`qwen2.5:3b` / `qwen2.5:7b`) and local `faster-whisper`. Optional cloud API keys can be added to your own local `settings.json`.

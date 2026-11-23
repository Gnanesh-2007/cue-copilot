"""
Session transcript: every question heard and every answer given is saved to
sessions/session_YYYYMMDD_HHMMSS.md (created lazily, so empty launches leave no file).
Open the folder from the HUD's 📝 button.
"""
import atexit
import os
import threading
import time

SESS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")


class SessionLog:
    def __init__(self):
        self.start = time.time()
        self.path = os.path.join(SESS_DIR, time.strftime("session_%Y%m%d_%H%M%S.md"))
        self.n_answers = 0
        self._started = False
        self._closed = False
        self._lock = threading.Lock()
        atexit.register(self.close)

    def _write(self, text: str):
        with self._lock:
            if not self._started:
                os.makedirs(SESS_DIR, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(f"# Cue Copilot session - {time.strftime('%Y-%m-%d %H:%M')}\n\n")
                self._started = True
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)

    def _stamp(self) -> str:
        s = int(time.time() - self.start)
        return f"[{s // 60:02d}:{s % 60:02d}]"

    def heard(self, text: str, who: str = "Heard"):
        if text and text.strip():
            self._write(f"{self._stamp()} {who}: {text.strip()}\n\n")

    def qa(self, question: str, answer: str, mode: str = "verbal"):
        answer = (answer or "").strip()
        if not answer:
            return
        self.n_answers += 1
        label = "Screen" if mode == "screen" else "Q"
        q_line = question.strip().splitlines()[0] if question.strip() else ""
        self._write(
            f"## {self._stamp()} {label} {self.n_answers}: {q_line}\n\n"
            f"{answer}\n\n---\n\n"
        )

    def close(self):
        if self._closed or not self._started:
            return
        self._closed = True
        mins = (time.time() - self.start) / 60
        try:
            self._write(f"Session length: {mins:.1f} min | Answers given: {self.n_answers}\n")
        except Exception:
            pass

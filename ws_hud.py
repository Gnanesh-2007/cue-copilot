"""
WebSocket Bridge for the Electron Frosted-Glass Cluely HUD (glass_ui/).
Implements the exact same public API as StealthHUD (overlay_ui.py) so app.py
can run the real Windows 11 Acrylic Frosted-Glass UI with zero backend changes.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GLASS_DIR = os.path.join(BASE_DIR, "glass_ui")
ELECTRON_EXE = os.path.join(GLASS_DIR, "node_modules", "electron", "dist", "electron.exe")


def glass_available() -> bool:
    return os.path.isfile(ELECTRON_EXE) and os.path.isfile(os.path.join(GLASS_DIR, "main.js"))


def _pick_free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
        except OSError:
            continue
    return 8765


class GlassHUD:
    def __init__(
        self,
        on_force_answer=None,
        on_screen_ocr=None,
        on_clear_memory=None,
        on_refine=None,
        on_mode_change=None,
        on_region_capture=None,
        on_ask=None,
        on_mic_toggle=None,
        on_profile_save=None,
        on_resume_pick=None,
        on_resume_clear=None,
        on_predict=None,
        on_docs_add=None,
        on_docs_delete=None,
        on_toggle_model=None,
    ):
        self.on_force_answer = on_force_answer
        self.on_screen_ocr = on_screen_ocr
        self.on_clear_memory = on_clear_memory
        self.on_refine = on_refine
        self.on_mode_change = on_mode_change
        self.on_region_capture = on_region_capture
        self.on_ask = on_ask
        self.on_mic_toggle = on_mic_toggle
        self.on_profile_save = on_profile_save
        self.on_resume_pick = on_resume_pick
        self.on_resume_clear = on_resume_clear
        self.on_predict = on_predict
        self.on_docs_add = on_docs_add
        self.on_docs_delete = on_docs_delete
        self.on_toggle_model = on_toggle_model

        self.mic_on = True
        self.port = _pick_free_port(8765)
        self._clients = set()
        self._loop = None
        self._proc = None
        self._running = True
        self._init_state = {}  # cached state replayed when Electron connects

        self._ws_thread = threading.Thread(target=self._run_ws_server, daemon=True)
        self._ws_thread.start()

    def _run_ws_server(self):
        import websockets

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def handler(ws):
            self._clients.add(ws)
            # Replay initial metadata/profile/status/docs so UI starts populated
            for key in ("meta", "profile", "status", "docs"):
                if key in self._init_state:
                    try:
                        await ws.send(json.dumps(self._init_state[key]))
                    except Exception:
                        pass
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        self._dispatch_from_ui(msg)
                    except Exception as e:
                        print(f"[GlassHUD] Message error: {e}")
            finally:
                self._clients.discard(ws)

        async def serve():
            async with websockets.serve(handler, "127.0.0.1", self.port):
                while self._running:
                    await asyncio.sleep(0.2)

        try:
            self._loop.run_until_complete(serve())
        except Exception as e:
            print(f"[GlassHUD] WebSocket server exited: {e}")

    def _dispatch_from_ui(self, msg: dict):
        t = msg.get("type")

        def run(fn, *args):
            if fn:
                threading.Thread(target=fn, args=args, daemon=True).start()

        if t == "force_answer":
            run(self.on_force_answer)
        elif t == "screen_ocr":
            run(self.on_screen_ocr)
        elif t == "clear_memory":
            run(self.on_clear_memory)
        elif t == "refine":
            run(self.on_refine, msg.get("kind", "shorter"))
        elif t == "mode_change":
            run(self.on_mode_change, bool(msg.get("auto", True)))
        elif t == "mic_toggle":
            self.mic_on = bool(msg.get("on", True))
            run(self.on_mic_toggle, self.mic_on)
        elif t == "ask":
            run(self.on_ask, msg.get("prompt", ""), msg.get("label", ""))
        elif t == "profile_save":
            run(self.on_profile_save, msg.get("fields") or {})
        elif t == "resume_pick":
            run(self.on_resume_pick, msg.get("path", ""))
        elif t == "resume_clear":
            run(self.on_resume_clear)
        elif t == "predict":
            run(self.on_predict)
        elif t == "docs_add":
            run(self.on_docs_add, msg.get("paths") or [])
        elif t == "docs_delete":
            run(self.on_docs_delete, msg.get("name", ""))
        elif t == "toggle_model":
            run(self.on_toggle_model)
        elif t == "quit":
            self._running = False

    def _emit(self, payload: dict):
        if not self._loop or not self._clients:
            return
        data = json.dumps(payload)

        async def _broadcast():
            for ws in list(self._clients):
                try:
                    await ws.send(data)
                except Exception:
                    self._clients.discard(ws)

        try:
            asyncio.run_coroutine_threadsafe(_broadcast(), self._loop)
        except Exception:
            pass

    # ---- Public API matching StealthHUD ----

    def set_meta(self, model_name: str, doc_count: int, vision: str | None = None):
        msg = {"type": "meta", "model": model_name, "docs": doc_count, "vision": vision}
        self._init_state["meta"] = msg
        self._emit(msg)

    def set_profile(self, profile: dict):
        msg = {"type": "profile", "profile": profile}
        self._init_state["profile"] = msg
        self._emit(msg)

    def set_setup_status(self, text: str, color_key: str = "good"):
        self._emit({"type": "setup_status", "text": text, "color_key": color_key})

    def set_log_dir(self, path: str):
        pass

    def set_status(self, text: str, color: str = "#4ECCA3"):
        msg = {"type": "status", "text": text, "color": color}
        self._init_state["status"] = msg
        self._emit(msg)

    def set_level(self, rms: float):
        self._emit({"type": "level", "rms": float(rms)})

    def update_live_caption(self, text: str):
        self._emit({"type": "live_caption", "text": text})

    def start_stream(self, question_text: str):
        self._emit({"type": "start_stream", "question": question_text})

    def append_token(self, token: str):
        self._emit({"type": "token", "token": token})

    def update_cues(self, full_text: str):
        self._emit({"type": "cues", "text": full_text})

    def add_transcript(self, text: str, speaker: str = "Interviewer"):
        self._emit({"type": "transcript", "text": text, "speaker": speaker})

    def set_docs(self, docs: list[dict]):
        msg = {"type": "docs", "docs": docs}
        self._init_state["docs"] = msg
        self._emit(msg)

    def set_sources(self, sources: list[str]):
        self._emit({"type": "sources", "sources": sources})

    def mainloop(self):
        """Launches Electron Frosted-Glass HUD and blocks until the user closes it."""
        # Wait briefly for WS server to bind
        time.sleep(0.25)
        env = os.environ.copy()
        env["CUE_WS_PORT"] = str(self.port)

        creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        self._proc = subprocess.Popen(
            [ELECTRON_EXE, GLASS_DIR],
            cwd=GLASS_DIR,
            env=env,
            creationflags=creationflags,
        )
        try:
            self._proc.wait()
        finally:
            self._running = False
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

"""
Cue Copilot - Beast Mode V2 Master Orchestrator
Pipeline:
  WASAPI Loopback Audio -> Adaptive Noise-Floor + Pre-Buffer -> Faster-Whisper (Silero VAD)
  -> Intelligent Question Filter -> Local RAG (context/ PDFs & code)
  -> Sliding Memory + Live Streaming Ollama (RTX 3050) -> Stealth HUD Overlay

Global Hotkeys (work from Google Meet, VS Code, or any app):
  F7  : Force Answer Now (instantly processes last spoken audio)
  F8  : Stealth Hide / Show HUD
  F9  : Screen-Snap OCR (reads coding problem / slide on screen & solves it)
  F10 : Toggle Click-Through Mode
"""
import os
import re
import glob
import site
import threading
import time
import io


def _add_cuda_dll_dirs():
    """Lets CTranslate2 find pip-installed NVIDIA cuBLAS/cuDNN so Whisper can run on the GPU."""
    bases = list(site.getsitepackages()) + [site.getusersitepackages()]
    for base in bases:
        for d in glob.glob(os.path.join(base, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


_add_cuda_dll_dirs()
import wave
from collections import deque
import numpy as np
import pyaudiowpatch as pyaudio

from overlay_ui import StealthHUD, SETTINGS_PATH
from ws_hud import GlassHUD, glass_available
from brain import CopilotBrain
from context_loader import LocalContextRAG
from screen_ocr import capture_image, ocr_image, image_to_b64
from brain import classify_screen
from question_filter import classify, is_followup
from session_log import SessionLog, SESS_DIR
from streaming_stt import DeepgramStreamer, load_deepgram_key
import profile_store

# Whisper models to try, best accuracy first. Falls back automatically if a download fails.
# (model, device, compute_type) tried in order. GPU first (small.en = 0.21s for a 7s question on the
# RTX 3050 vs ~1.1s on CPU), then CPU fallbacks. int8_float16 keeps VRAM use ~0.5GB so a 7B LLM still fits.
# To try an even more accent-robust model later: ("distil-large-v3", "cuda", "int8_float16").
WHISPER_CANDIDATES = [
    ("small.en", "cuda", "int8_float16"),
    ("distil-small.en", "cuda", "int8_float16"),
    ("small.en", "cpu", "int8"),
    ("base.en", "cpu", "int8"),
]

MID_SPEECH_ANSWER = False  # True = start answering before the speaker pauses (faster but can answer half a sentence)
SECTION_HEADS = {"DIRECT ANSWER", "KEY POINTS", "WHAT THIS SHOWS", "APPROACH", "CODE", "FIX",
                 "HR QUESTIONS", "TECHNICAL QUESTIONS", "PROJECT QUESTIONS", "THEORY QUESTIONS",
                 "CORE QUESTIONS", "SCENARIO QUESTIONS"}
END_PAUSE_SEC = 1.4       # wait 1.4s of silence so normal mid-question breaths never cut off a question
LIVE_TICK_SEC = 0.45       # snappy live caption interval on CUDA Whisper while someone is talking

# Whisper invents these on music / noise / breaths; never treat them as something the interviewer said
_CALL_HALLUCINATIONS = {"thank you", "thanks for watching", "thank you for watching", "you", "bye",
                        "please subscribe", "subscribe", "okay", "ok", "so", "uh", "um", "hmm", "yeah"}


def _audio_device_signature():
    """IDs of every ACTIVE playback/recording device plus the current defaults, straight from Windows
    (a few ms). Changes when Meet/Zoom flips Bluetooth earbuds into Hands-Free mode, when earbuds connect /
    disconnect, or when the default mic/speaker changes. None if it can't be read."""
    try:
        from pycaw.pycaw import AudioUtilities
        enum = AudioUtilities.GetDeviceEnumerator()
        ids = []
        for flow in (0, 1):                                   # eRender, eCapture
            coll = enum.EnumAudioEndpoints(flow, 1)            # DEVICE_STATE_ACTIVE
            ids += sorted(coll.Item(i).GetId() for i in range(coll.GetCount()))
            for role in (0, 2):                                # eConsole, eCommunications defaults
                try:
                    ids.append(f"default{flow}{role}:" + enum.GetDefaultAudioEndpoint(flow, role).GetId())
                except Exception:
                    ids.append(f"default{flow}{role}:none")
        return tuple(ids)
    except Exception:
        return None


class CueCopilotApp:
    def __init__(self):
        self.running = True
        self.whisper_model = None
        self.sample_rate = 48000
        self.channels = 2

        # Rolling history of the last ~14 seconds of call audio (16 kHz mono chunks) for F7 Force-Answer
        self.rolling16 = deque(maxlen=140)
        self.audio_lock = threading.Lock()
        self.force_trigger_event = threading.Event()

        # Incomplete-sentence stitcher (holds unfinished thoughts across thinking pauses)
        self.pending_fragment = ""
        self.pending_time = 0.0

        # Real-time live captioning & instant mid-speech question trigger state
        self._live_busy = False
        self._last_answered_norm = ""
        self._last_answered_time = 0.0

        # Load local RAG (context/ PDFs, DOCX, code)
        self.rag = LocalContextRAG()

        # Initialize streaming AI brain with conversation memory
        self.brain = CopilotBrain(rag=self.rag)

        # Initialize Frosted-Glass Electron HUD (falls back to classic V6 Tkinter HUD if unavailable or CUE_CLASSIC_UI=1)
        hud_cls = GlassHUD if (glass_available() and not os.environ.get("CUE_CLASSIC_UI")) else StealthHUD
        hud_kwargs = dict(
            on_force_answer=self.trigger_force_answer,
            on_screen_ocr=self.trigger_screen_ocr,
            on_clear_memory=self.clear_all,
            on_refine=self.trigger_refine,
            on_mode_change=self._set_auto_mode,
            on_region_capture=self.trigger_screen_ocr,
            on_ask=self.trigger_ask,
            on_mic_toggle=self._set_mic,
            on_profile_save=self.save_setup,
            on_resume_pick=self.load_resume,
            on_resume_clear=self.clear_resume,
            on_predict=self.predict_questions,
        )
        if hud_cls is GlassHUD:
            hud_kwargs["on_docs_add"] = self.add_docs
            hud_kwargs["on_docs_delete"] = self.delete_doc
            hud_kwargs["on_toggle_model"] = self.toggle_model
        self.hud = hud_cls(**hud_kwargs)
        # Interview setup (resume, job description, company, round) from the HUD's Setup tab
        self.profile = profile_store.load_profile()
        self._apply_profile()
        self.hud.set_profile(self.profile)
        if hasattr(self.hud, "set_docs") and hasattr(self.rag, "list_files"):
            self.hud.set_docs(self.rag.list_files())
        self.mic_enabled = self.hud.mic_on
        self.transcript_lines = deque(maxlen=30)   # what the interviewer said, for the Ask box
        self.heard_log = deque(maxlen=80)          # (time, speaker, text) for F7 "answer the last minute"
        self._open_seg = np.zeros(0, dtype=np.float32)   # call audio still being spoken (not yet a sentence)
        self.hud.set_meta(self.brain.model, self.rag.file_count, self.brain.vision_model)
        self.session = SessionLog()          # saves every Q&A to sessions/*.md
        self.hud.set_log_dir(SESS_DIR)
        self.auto_mode = True
        self.last_question = None  # (question_text, mode) for the Shorter/Deeper/Example/Simpler chips

        # True word-by-word streaming STT via Deepgram Nova-2 WebSocket (optional; auto-falls back to local CUDA Whisper)
        self.dg = None
        dg_key = load_deepgram_key(SETTINGS_PATH)
        if dg_key:
            self.dg = DeepgramStreamer(
                dg_key,
                on_interim=self._on_dg_interim,
                on_final=self._on_dg_final,
            )
            self.dg.start()

    def _set_auto_mode(self, auto: bool):
        self.auto_mode = auto

    def clear_all(self):
        """Clears conversation memory, pending fragments, and active question state."""
        self.brain.cancel_current()
        self.brain.clear_memory()
        self.pending_fragment = ""
        self.pending_time = 0.0
        self._last_answered_norm = ""
        self._last_answered_time = 0.0
        self.last_question = None
        if hasattr(self.hud, "set_sources"):
            self.hud.set_sources([])
        self.hud.set_status("🟢 LISTENING", "#4ECCA3")
        print("[App] Memory and HUD state cleared.")

    def add_docs(self, paths: list[str]):
        """Copies uploaded PDF/DOCX/TXT files into context/ and reloads Hybrid RAG."""
        if not paths or not hasattr(self.rag, "add_files"):
            return
        added = self.rag.add_files(paths)
        if hasattr(self.hud, "set_docs"):
            self.hud.set_docs(self.rag.list_files())
        self.hud.set_meta(self.brain.model, self.rag.file_count, self.brain.vision_model)
        self.hud.set_setup_status(f"Added {added} file(s) ✓ ({self.rag.file_count} total)", "good")

    def delete_doc(self, filename: str):
        """Deletes a stored file from context/ and reloads Hybrid RAG."""
        if not filename or not hasattr(self.rag, "delete_file"):
            return
        if self.rag.delete_file(filename):
            if hasattr(self.hud, "set_docs"):
                self.hud.set_docs(self.rag.list_files())
            self.hud.set_meta(self.brain.model, self.rag.file_count, self.brain.vision_model)
            self.hud.set_setup_status(f"Deleted {filename}", "dim")

    def toggle_model(self):
        """1-Click toggle between Frontier Cloud (Claude/Gemini/GPT-4o) and Local Ollama (₹0)."""
        model, vision, is_cloud = self.brain.toggle_backend()
        self.hud.set_meta(model, self.rag.file_count, vision)
        if not is_cloud:
            threading.Thread(target=self._warmup_ollama, daemon=True).start()
        print(f"[Brain] Switched to {'Cloud' if is_cloud else 'Local'}: {model}")

    REFINE_PROMPTS = {
        "shorter": "Answer again in at most 2 short sentences, no bullets.",
        "deeper": "Answer again with more technical depth, including one real-world trade-off.",
        "example": "Give one concrete real-world example that illustrates this answer.",
        "simpler": "Explain it again in very simple words, as if to a beginner.",
    }

    def _note_heard(self, text: str):
        """Records what was heard on the call (Transcript tab, Ask-box context, saved session)."""
        text = (text or "").strip()
        if not text:
            return
        self.transcript_lines.append(f"Interviewer: {text}")
        self.heard_log.append((time.time(), "Interviewer", text))
        self.hud.add_transcript(text, "Interviewer")
        self.session.heard(text, "Interviewer")

    def _note_you(self, text: str):
        """Records what the candidate (you) said, from the microphone."""
        self.transcript_lines.append(f"You: {text}")
        self.heard_log.append((time.time(), "You", text))
        self.hud.add_transcript(text, "You")
        self.session.heard(text, "You")
        self.brain.note_candidate_said(text)

    def _set_mic(self, on: bool):
        self.mic_enabled = on

    # ------------------------------------------------------------- interview setup
    def _apply_profile(self):
        """Pushes the saved setup into the AI prompt and the Whisper vocabulary."""
        self.brain.profile_block = profile_store.prompt_block(self.profile)
        self.brain.has_resume = bool((self.profile.get("resume_text") or "").strip())
        self.brain.round = self.profile.get("round") or "Mixed"
        self.brain.has_notes = len((self.profile.get("notes") or "").split()) >= 8   # enough to describe projects
        self.profile_vocab = profile_store.vocab_terms(self.profile, max_terms=30)

    def save_setup(self, fields: dict):
        self.profile.update(fields)
        try:
            profile_store.save_profile(self.profile)
        except Exception as e:
            self.hud.set_setup_status(f"Could not save: {e}", "err")
            return
        self._apply_profile()
        self.hud.set_profile(self.profile)
        self.hud.set_setup_status("Saved ✓  answers now use this setup", "good")
        print(f"[Setup] saved: {profile_store.summary(self.profile) or 'empty'}")

    def load_resume(self, path: str):
        try:
            text = profile_store.read_resume(path)
        except Exception as e:
            self.hud.set_setup_status(f"Could not read that file: {e}", "err")
            return
        if len(text) < 80:
            self.hud.set_setup_status("No text found in that file (scanned image PDF?). Try DOCX or a text PDF.",
                                      "warn")
            return
        self.profile["resume_name"] = os.path.basename(path)
        self.profile["resume_text"] = text
        profile_store.save_profile(self.profile)
        self._apply_profile()
        self.hud.set_profile(self.profile)
        self.hud.set_setup_status("Resume added ✓", "good")
        print(f"[Setup] resume: {os.path.basename(path)} ({len(text)} chars)")

    def clear_resume(self):
        self.profile["resume_name"] = ""
        self.profile["resume_text"] = ""
        profile_store.save_profile(self.profile)
        self._apply_profile()
        self.hud.set_profile(self.profile)
        self.hud.set_setup_status("Resume removed", "dim")

    def predict_questions(self):
        """Setup tab button: likely HR / technical / project questions with short answers to rehearse."""
        if not profile_store.is_filled(self.profile):
            self.hud.set_setup_status("Add a resume or job description first", "warn")
            return
        title = "Likely questions" + (f" · {profile_store.summary(self.profile)}"
                                      if profile_store.summary(self.profile) else "")
        self.hud.start_stream(title)
        self.hud.set_status("🧠 PREPARING...", "#A78BFA")
        out = []
        for token in self._clean_stream(self.brain.stream_answer(
                "Predict my likely interview questions with short answers.", mode="prep", interrupt=True)):
            out.append(token)
            self.hud.append_token(token)
        note = ("\n\n⚠ These are practice answers. The model can add details you never did (tools, numbers, "
                "stories): change anything that isn't true for you before you rehearse it.")
        self.hud.append_token(note)
        self.session.qa(title, "".join(out) + note, "prep")
        self.hud.set_status("🟢 LISTENING", "#4ECCA3")

    def trigger_ask(self, prompt: str, label: str):
        """Ask box / Follow-up chips: answer a typed follow-up using the last Q&A and recent call context."""
        ctx = "\n".join(list(self.transcript_lines)[-10:])
        prev_qa = ""
        if self.brain.history:
            last = self.brain.history[-1]
            prev_qa = f"[Previous Q&A being followed up on]\nQ: {last['q']}\nA: {last['a']}\n\n"
        full = (
            prev_qa
            + (f"[Recent conversation heard on the call]\n{ctx}\n\n" if ctx else "")
            + f"[Follow-up / User request: build directly on the previous Q&A if relevant, never reply SKIP]\n{prompt}"
        )
        self.last_question = (full, "verbal")
        self.hud.start_stream(label)
        self.hud.set_status("🧠 ANALYZING...", "#A78BFA")
        out = []
        sources_sent = False
        for token in self._clean_stream(self.brain.stream_answer(full, mode="verbal", interrupt=True)):
            if not sources_sent and hasattr(self.hud, "set_sources"):
                self.hud.set_sources(self.brain.last_sources)
                sources_sent = True
            out.append(token)
            self.hud.append_token(token)
        self.session.qa(label, "".join(out), "verbal")
        self.hud.set_status("🟢 LISTENING", "#4ECCA3")

    def trigger_refine(self, kind: str):
        """HUD chips: re-answer the last question with a style instruction."""
        if not self.last_question:
            return
        q, mode = self.last_question
        instr = self.REFINE_PROMPTS.get(kind, "")
        self.hud.start_stream(f"{q.splitlines()[0][:90]}  ↻ {kind}")
        self.hud.set_status("🧠 REFINING...", "#A78BFA")
        out = []
        for token in self._clean_stream(self.brain.stream_answer(f"{q}\n\n[Instruction: {instr}]", mode=mode,
                                                                 interrupt=True)):
            out.append(token)
            self.hud.append_token(token)
        self.session.qa(f"{q.splitlines()[0][:120]} ({kind})", "".join(out), mode)
        self.hud.set_status("🟢 LISTENING", "#4ECCA3")

    def _find_loopback_device(self, p):
        try:
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])

            for dev in p.get_device_info_generator():
                if dev["isLoopbackDevice"] and default_speakers["name"] in dev["name"]:
                    return dev

            for dev in p.get_device_info_generator():
                if dev["isLoopbackDevice"]:
                    return dev
        except Exception as e:
            print(f"[Audio] Loopback lookup error: {e}")
        return None

    def _load_whisper(self):
        """Loads faster-whisper base.en on CPU int8 (4x faster live captions, leaves full 6GB VRAM for Ollama)."""
        from faster_whisper import WhisperModel

        self.hud.set_status("⏳ LOADING WHISPER...", "#60A5FA")
        last_err = None
        cuda_broken = False
        for name, device, ctype in WHISPER_CANDIDATES:
            if device == "cuda" and cuda_broken:
                continue
            try:
                model = WhisperModel(name, device=device, compute_type=ctype, cpu_threads=6)
                # force a tiny run so a missing CUDA library fails here, not mid-interview
                list(model.transcribe(np.zeros(8000, dtype=np.float32), language="en")[0])
                print(f"[Whisper] Loaded {name} on {device.upper()} ({ctype})")
                self.hud.set_status("🟢 LISTENING", "#4ECCA3")
                return model
            except Exception as e:
                last_err = e
                print(f"[Whisper] {name} ({device}) failed: {e}")
                if device == "cuda":
                    cuda_broken = True
        raise last_err

    def _warmup_ollama(self):
        """Pre-loads the local Ollama model into RTX 3050 VRAM (skipped when Frontier Cloud is active)."""
        if getattr(self.brain, "cloud_enabled", False):
            print(f"[Brain] Using Frontier Cloud: {self.brain.cloud_provider} ({self.brain.cloud_model})")
            return
        import requests
        try:
            requests.post(
                "http://127.0.0.1:11434/api/generate",
                json={
                    "model": self.brain.model,
                    "prompt": "hi",
                    "stream": False,
                    "keep_alive": "30m",
                    "options": {"num_predict": 1, "num_ctx": 4096},
                },
                timeout=15,
            )
            print(f"[Brain] Pre-warmed {self.brain.model} in GPU VRAM.")
        except Exception:
            pass

    def _transcribe_bytes(self, raw_bytes: bytes, beam_size: int = 2, rate: int | None = None,
                          channels: int | None = None) -> str:
        """Converts raw WASAPI int16 audio directly to 16kHz float32 mono numpy for Whisper (bypasses PyAV).
        rate/channels default to the call-audio format; the microphone passes its own."""
        if not self.whisper_model or not raw_bytes:
            return ""
        samples = self._to_16k_mono(raw_bytes, rate or self.sample_rate, channels or self.channels)
        return self._transcribe_audio(samples, beam_size=beam_size, vad=True)

    @staticmethod
    def _to_16k_mono(raw_bytes: bytes, rate: int, channels: int) -> np.ndarray:
        """int16 WASAPI bytes -> float32 mono 16 kHz (what Whisper and Silero VAD expect)."""
        samples = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            valid_len = (len(samples) // channels) * channels
            samples = samples[:valid_len].reshape(-1, channels).mean(axis=1)
        if rate == 16000 or not len(samples):
            return samples.astype(np.float32)
        if rate % 16000 == 0:                         # 48k -> 16k: average each group (no chunk-edge clicks)
            k = rate // 16000
            n = (len(samples) // k) * k
            return samples[:n].reshape(-1, k).mean(axis=1).astype(np.float32)
        import math
        from scipy.signal import resample_poly
        g = math.gcd(16000, rate)
        return resample_poly(samples, 16000 // g, rate // g).astype(np.float32)

    def _transcribe_audio(self, samples: np.ndarray, beam_size: int = 3, vad: bool = False) -> str:
        """Whisper on 16 kHz mono audio. Call audio arrives already cut into sentences by Silero VAD."""
        if not self.whisper_model or samples is None or len(samples) < 1600:
            return ""
        peak = float(np.max(np.abs(samples)))
        if 0 < peak < 0.25:                           # quiet video / low call volume: normalise loudness
            samples = samples * min(0.5 / peak, 20.0)
        # Only vocabulary here: example sentences in the prompt make Whisper repeat them on unclear audio
        extra = self.rag.vocab_hint() if hasattr(self.rag, "vocab_hint") else ""
        pv = getattr(self, "profile_vocab", "")
        if pv:                                        # company, tools and projects from the Setup tab
            extra = (extra + f" Names and terms: {pv}.").strip()
        tech_prompt = (extra + " ") + (
            "Software engineering interview, Indian English. Terms: OOP, polymorphism, inheritance, "
            "DBMS, SQL, NoSQL, ACID, normalization, OS, process, thread, deadlock, TCP, UDP, HTTP, REST API, "
            "Java, Python, C++, JavaScript, React, Node.js, Spring Boot, Django, AWS, GCP, Docker, Kubernetes, "
            "Git, CI/CD, data structures, algorithms, linked list, hash map, binary tree, DP, Big O, "
            "microservices, load balancer, caching, Redis, MongoDB, PostgreSQL, machine learning."
        )
        segments, _ = self.whisper_model.transcribe(
            samples,
            language="en",
            beam_size=beam_size,
            initial_prompt=tech_prompt,
            condition_on_previous_text=False,
            vad_filter=vad,
            vad_parameters=dict(min_silence_duration_ms=300) if vad else None,
            without_timestamps=True,
            temperature=0.0,
        )
        parts = []
        for s in segments:
            if s.no_speech_prob > 0.6 and s.avg_logprob < -0.8:   # music / noise, not words
                continue
            parts.append(s.text)
        return " ".join(parts).strip()

    @staticmethod
    def _clean_stream(tokens):
        """Stops the answer if the model starts echoing an input block like "[Already said: ...]",
        or starts repeating itself with a summary section after the CODE section."""
        full, sent = "", 0                 # text received / text already shown
        primary = ("DIRECT ANSWER", "WHAT THIS SHOWS", "APPROACH")
        after_code = ("KEY POINTS", "DIRECT ANSWER", "Direct answer", "Summary", "Explanation", "WHAT THIS SHOWS")
        echo = ("[Already", "[What the candidate", "[Recent conversation", "[Candidate Project")
        watch = primary + after_code + echo + tuple(SECTION_HEADS)

        def must_stop(text):
            lines = [ln.strip() for ln in text.split("\n")]
            if any(ln.startswith(echo) for ln in lines):
                return True
            heads = [ln.rstrip(":").upper() for ln in lines]
            if sum(h in primary for h in heads) >= 2:          # a second answer layout started
                return True
            sections = [h for h in heads if h in SECTION_HEADS]
            if len(sections) != len(set(sections)):           # the same section started again
                return True
            if "CODE" in heads:
                i = heads.index("CODE")
                if any(ln.startswith(after_code) for ln in lines[i + 1:]):
                    return True
            return False

        for tok in tokens:
            full += tok
            # keep only the part we know is complete: stop text lives on a line of its own
            cut = full.rfind("\n") + 1
            done = full[:cut]
            if must_stop(done):
                keep = done.rstrip("\n").rsplit("\n", 1)[0] if "\n" in done.rstrip("\n") else ""
                if len(keep) > sent:
                    yield keep[sent:]
                return
            cur = full[cut:].lstrip()
            # the unfinished last line could still become a stop heading: hold it back for now
            if cur and any(w.startswith(cur) or cur.startswith(w) for w in watch):
                upto = cut
            else:
                upto = len(full)
            if upto > sent:
                yield full[sent:upto]
                sent = upto
        end = full.rsplit("\n", 1)[0] if must_stop(full) and "\n" in full else full
        if len(end) > sent:
            yield end[sent:]

    def _stream_to_hud(self, question: str, mode: str = "verbal", force: bool = False, display_label: str = ""):
        """
        Streams tokens live from Ollama straight into the HUD.
        In automatic verbal mode, checks the first few chars for [SKIP] before clearing the HUD
        so casual banter never overwrites your previous answer.
        """
        shown_q = display_label or question
        self.last_question = (question, mode)
        self.hud.set_status("🧠 ANALYZING...", "#A78BFA")
        # what was said just before this question (names, branch, skills...) so answers use real facts
        ctx = "\n".join(list(self.transcript_lines)[-9:-1]) if mode == "verbal" else ""

        if force or mode == "screen":
            self.hud.start_stream(shown_q)
            self.hud.set_status("🧠 STREAMING...", "#A78BFA")
            out = []
            sources_sent = False
            for token in self._clean_stream(self.brain.stream_answer(question, mode=mode, interrupt=True,
                                                                     context=ctx)):
                if not sources_sent and hasattr(self.hud, "set_sources"):
                    self.hud.set_sources(self.brain.last_sources)
                    sources_sent = True
                out.append(token)
                self.hud.append_token(token)
            self.session.qa(shown_q, "".join(out), mode)
            self.hud.set_status("🟢 LISTENING", "#4ECCA3")
            return True

        # Intelligent AI intent gate: peek first ~6 chars to detect SKIP / [SKIP]
        peek_buf = ""
        started = False
        out = []
        for token in self._clean_stream(self.brain.stream_answer(question, mode=mode, interrupt=False,
                                                                 context=ctx)):
            if not started:
                peek_buf += token
                stripped_up = peek_buf.lstrip().upper()
                if stripped_up.startswith("SKIP") or stripped_up.startswith("[SKIP"):
                    print(f"[AI Skipped / Held Fragment]: {question}")
                    self.hud.set_status("🟢 LISTENING", "#4ECCA3")
                    return False
                # Still could be building "SKIP" or "[SKIP]" prefix
                if len(stripped_up) < 6 and ("SKIP".startswith(stripped_up) or "[SKIP]".startswith(stripped_up)):
                    continue
                # Confirmed genuine question -> clear HUD, show Hybrid RAG sources, and flush buffered text
                started = True
                self.hud.start_stream(question)
                if hasattr(self.hud, "set_sources"):
                    self.hud.set_sources(self.brain.last_sources)
                self.hud.set_status("🧠 STREAMING...", "#A78BFA")
                self.hud.append_token(peek_buf)
                out.append(peek_buf)
            else:
                out.append(token)
                self.hud.append_token(token)

        # Edge case: very short non-SKIP response that finished under 6 chars
        if not started and peek_buf.strip() and not peek_buf.lstrip().upper().startswith(("SKIP", "[SKIP")):
            self.hud.start_stream(question)
            self.hud.append_token(peek_buf)
            out.append(peek_buf)
            started = True

        if started:
            self.session.qa(question, "".join(out), mode)
        self.hud.set_status("🟢 LISTENING", "#4ECCA3")
        return started

    FORCE_WINDOW_SEC = 60

    def trigger_force_answer(self):
        """F7 / Answer button: answer whatever was asked or explained in the last minute, including a
        problem described over several sentences (requirements, constraints) and the sentence still being said."""
        if not self.whisper_model:
            return
        self.brain.cancel_current()
        self.hud.set_status("⚡ FORCE CAPTURING...", "#F59E0B")

        # 1) the sentence still being spoken (not yet cut by the VAD), if any
        tail = self._open_seg
        if tail is not None and len(tail) >= 16000:
            text = self._transcribe_audio(tail[-20 * 16000:], beam_size=2, vad=True)
            if text and re.sub(r"[^a-z' ]", "", text.lower()).strip() not in _CALL_HALLUCINATIONS:
                print(f"[F7 unfinished sentence]: {text}")
                self._note_heard(text)

        # 2) everything heard in the last minute
        now = time.time()
        recent = [(t, who, txt) for (t, who, txt) in list(self.heard_log) if now - t <= self.FORCE_WINDOW_SEC]
        if not recent:
            self.hud.update_cues("Nothing heard in the last minute yet. Type your question in the Ask box instead.")
            self.hud.set_status("🟢 LISTENING", "#4ECCA3")
            return
        convo = "\n".join(f"[{int(now - t)}s ago] {who}: {txt}" for (t, who, txt) in recent)
        prompt = f"[Last minute of the interview]\n{convo}\n\n[Answer what the interviewer wants right now.]"
        # label: the newest real question, else the newest line with substance (not "Essentially,")
        label = ""
        for (_, who, txt) in reversed(recent):
            verdict, q = classify(txt)
            if verdict == "question":
                label = q
                break
        if not label:
            label = next((txt for (_, _, txt) in reversed(recent) if len(txt.split()) >= 6), recent[-1][2])
        label = f"⚡ Last {int(now - recent[0][0]) + 1}s: " + (label if len(label) < 90 else label[:87] + "…")
        print(f"[F7] answering from {len(recent)} lines of the last minute")

        self.last_question = (prompt, "force")
        self.hud.start_stream(label)
        self.hud.set_status("🧠 STREAMING...", "#A78BFA")
        out = []
        for token in self._clean_stream(self.brain.stream_answer(prompt, mode="force", interrupt=True)):
            out.append(token)
            self.hud.append_token(token)
        self.session.qa(f"[F7] {label.split(': ', 1)[-1]}", "".join(out), "force")
        self.hud.set_status("🟢 LISTENING", "#4ECCA3")

    def trigger_screen_ocr(self, bbox=None):
        """
        F9 (whole screen, all monitors) / F12 or 🖼 (dragged region).
        With a vision model installed the AI SEES the screenshot (diagrams, code, MCQs, errors) and
        the OCR text is passed along as a hint. Without one, it solves from OCR text alone.
        """
        self.brain.cancel_current()
        self.hud.set_status("📸 READING SCREEN...", "#38BDF8")

        img = capture_image(bbox)
        if img is None:
            self.hud.update_cues("⚠️ Could not capture the screen.")
            self.hud.set_status("🟢 LISTENING", "#4ECCA3")
            return

        screen_text = ocr_image(img)
        # Right tool for the screen: text-heavy screens (MCQ, code, errors, theory) are read with OCR and
        # answered by the stronger/faster text model; diagrams and image-heavy screens go to the vision model.
        kind = classify_screen(screen_text)
        # Frontier cloud models (Claude/Gemini/GPT-4o) see every screenshot directly with 0 VRAM swap;
        # local Ollama uses vision only for diagrams to avoid evicting the 7B text model.
        is_cloud = getattr(self.brain, "cloud_enabled", False)
        use_vision = is_cloud or (bool(self.brain.vision_model) and kind == "diagram")
        print(f"[Screen] classified as: {kind} -> {'vision model' if use_vision else 'OCR + text model'}")
        if not use_vision and len(screen_text.strip()) < 10:
            self.hud.update_cues("⚠️ No readable text on screen. Install a vision model: ollama pull qwen2.5vl:3b")
            self.hud.set_status("🟢 LISTENING", "#4ECCA3")
            return

        first_line = screen_text.splitlines()[0][:70] if screen_text.strip() else "(image)"
        tag = "👁 Vision" if use_vision else "📸 OCR"
        where = "region" if bbox else "screen"
        print(f"[Screen {where}] {tag}: {len(screen_text)} OCR chars, image {img.size}")
        self.hud.start_stream(f"{tag} {where}: {first_line}…")
        self.hud.set_status("👁 LOOKING..." if use_vision else "🧠 SOLVING SCREEN...", "#A78BFA")

        images = [image_to_b64(img)] if use_vision else None
        prompt = (f"[Screen Capture]\nOCR text hint (may contain errors):\n{screen_text}"
                  if screen_text.strip() else "[Screen Capture] (no OCR text; rely on the image)")
        out = []
        for token in self._clean_stream(self.brain.stream_answer(prompt, mode="screen", interrupt=True,
                                                                 images=images)):
            out.append(token)
            self.hud.append_token(token)
        self.session.qa(first_line, "".join(out), "screen")
        self.hud.set_status("🟢 LISTENING", "#4ECCA3")
        if use_vision:
            # the vision model evicted the text model from VRAM; reload it now so the next question is fast
            threading.Thread(target=self._warmup_ollama, daemon=True).start()

    def _audio_listener_worker(self):
        """
        Call audio (WASAPI loopback of every output: earbuds + speakers) -> 16 kHz mono stream ->
        Silero VAD decides where each sentence starts and ends (speech, not loudness, so background
        music / room noise in a video or call never keeps a sentence "open") -> Whisper -> question filter.
        Live captions are produced while the person is still talking.
        """
        from faster_whisper.vad import get_speech_timestamps, VadOptions

        try:
            self.whisper_model = self._load_whisper()
        except Exception as e:
            self.hud.set_status("🔴 WHISPER LOAD FAILED", "#FF5252")
            print(f"[Whisper] Load error: {e}")
            return

        SR = 16000
        vad_opts = VadOptions(threshold=0.5, min_speech_duration_ms=250, min_silence_duration_ms=450,
                              speech_pad_ms=150)
        state_lock = threading.Lock()
        state = {"speaking": False, "last_sound_time": 0.0}   # read by the mic echo guard
        dev_info = {}                      # idx -> (rate, channels, name)
        dev_q = {}                         # idx -> deque of (time, raw bytes, rms)
        aud = {"p": None, "streams": [], "mic": None}

        def make_callback(dev_idx):
            q = dev_q[dev_idx]

            def _callback(in_data, frame_count, time_info, status):
                samples = np.frombuffer(in_data, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
                q.append((time.time(), in_data, rms))
                return (None, pyaudio.paContinue)
            return _callback

        # ---- Your microphone ("You"): transcript + context only, never triggers an answer ----
        ms = {"speaking": False, "last": 0.0, "start": 0.0, "buf": [], "noise": 80.0,
              "pre": deque(maxlen=20), "rate": 48000, "ch": 1}
        mic_lock = threading.Lock()

        def mic_callback(in_data, frame_count, time_info, status):
            if not self.mic_enabled:
                return (None, pyaudio.paContinue)
            samples = np.frombuffer(in_data, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) > 0 else 0.0
            now = time.time()
            with mic_lock:
                if rms > max(ms["noise"] * 2.8, 220.0):
                    if not ms["speaking"]:
                        ms["speaking"] = True
                        ms["start"] = now
                        ms["buf"] = list(ms["pre"])
                    ms["buf"].append(in_data)
                    ms["last"] = now
                elif ms["speaking"]:
                    ms["buf"].append(in_data)
                else:
                    ms["noise"] = 0.97 * ms["noise"] + 0.03 * min(rms, 400.0)
                    ms["pre"].append(in_data)
            return (None, pyaudio.paContinue)

        def close_audio():
            for st in aud["streams"] + ([aud["mic"]] if aud["mic"] else []):
                try:
                    st.stop_stream()
                    st.close()
                except Exception:
                    pass
            aud["streams"], aud["mic"] = [], None
            if aud["p"] is not None:
                try:
                    aud["p"].terminate()
                except Exception:
                    pass
                aud["p"] = None

        def open_audio() -> bool:
            """(Re)opens a loopback stream on EVERY active output (earbuds, earbuds Hands-Free, speakers...)
            plus the current default microphone. PortAudio only sees new devices after a full re-init."""
            p = pyaudio.PyAudio()
            aud["p"] = p
            dev_info.clear()
            dev_q.clear()
            for dev in p.get_device_info_generator():
                if not dev.get("isLoopbackDevice"):
                    continue
                try:
                    ch, rate = int(dev["maxInputChannels"]), int(dev["defaultSampleRate"])
                    dev_info[dev["index"]] = (rate, ch, dev["name"])
                    dev_q[dev["index"]] = deque(maxlen=2000)
                    st = p.open(format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
                                input_device_index=dev["index"], frames_per_buffer=1024,
                                stream_callback=make_callback(dev["index"]))
                    st.start_stream()
                    aud["streams"].append(st)
                    print(f"[Audio] Monitoring loopback: {dev['name']} ({rate}Hz, {ch}ch)")
                except Exception as e:
                    dev_info.pop(dev["index"], None)
                    dev_q.pop(dev["index"], None)
                    print(f"[Audio] Could not open {dev['name']}: {e}")
            try:
                try:
                    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
                    mic_dev = p.get_device_info_by_index(wasapi["defaultInputDevice"])
                except Exception:
                    mic_dev = p.get_default_input_device_info()
                ms["rate"] = int(mic_dev["defaultSampleRate"])
                ms["ch"] = max(1, min(int(mic_dev["maxInputChannels"]), 2))
                with mic_lock:
                    ms["speaking"], ms["buf"] = False, []
                    ms["pre"].clear()
                mic = p.open(format=pyaudio.paInt16, channels=ms["ch"], rate=ms["rate"], input=True,
                             input_device_index=mic_dev["index"], frames_per_buffer=1024,
                             stream_callback=mic_callback)
                mic.start_stream()
                aud["mic"] = mic
                print(f"[Audio] Microphone ('You'): {mic_dev['name']} ({ms['rate']}Hz, {ms['ch']}ch)")
            except Exception as e:
                print(f"[Audio] Microphone unavailable, continuing with call audio only: {e}")
            return bool(aud["streams"])

        if not open_audio():
            self.hud.set_status("🔴 NO AUDIO OUTPUT", "#FF5252")
        else:
            self.hud.set_status("🟢 LISTENING", "#4ECCA3")

        com_ready = False
        try:
            import comtypes
            comtypes.CoInitialize()
            com_ready = True
        except Exception as e:
            print(f"[Audio] device-change watch off ({e}); restart the app after switching audio devices")
        dev_sig = _audio_device_signature() if com_ready else None
        last_dev_check = time.time()

        active = None                      # loopback device currently carrying the call
        active_last_sound = 0.0
        seg = np.zeros(0, dtype=np.float32)   # open (not yet flushed) call audio at 16 kHz
        last_vad = 0.0
        last_live = 0.0
        hearing = False

        while self.running:
            time.sleep(0.1)
            now = time.time()
            self._open_seg = seg                  # F7 reads the not-yet-finished sentence from here

            # ---------- audio devices changed? (earbuds switched to Hands-Free by Meet/Zoom,
            #            earbuds connected/disconnected, default mic changed) -> reopen everything ----------
            if com_ready and now - last_dev_check >= 2.0:
                last_dev_check = now
                sig = _audio_device_signature()
                if sig is not None and sig != dev_sig:
                    print("[Audio] Audio devices changed - reconnecting...")
                    self.hud.set_status("🔄 SWITCHING AUDIO...", "#38BDF8")
                    close_audio()
                    time.sleep(0.8)               # Windows needs a moment to finish the switch
                    ok = open_audio()
                    dev_sig = sig if ok else None   # failed: try again on the next check
                    active, seg, hearing = None, np.zeros(0, dtype=np.float32), False
                    self.hud.set_status("🟢 LISTENING" if ok else "🔴 NO AUDIO OUTPUT",
                                        "#4ECCA3" if ok else "#FF5252")
                    continue
            if not dev_q:
                continue

            # ---------- microphone segment finished? ----------
            mic_raw, mic_start = None, 0.0
            with mic_lock:
                if ms["speaking"]:
                    if now - ms["last"] >= 0.9 or now - ms["start"] >= 40.0:
                        ms["speaking"] = False
                        mic_raw, mic_start = b"".join(ms["buf"]), ms["start"]
                        ms["buf"] = []
            if mic_raw:
                with state_lock:
                    # echo guard: call audio playing while the mic heard sound = speaker leaking into the mic
                    echo = state["speaking"] or state["last_sound_time"] >= mic_start - 0.4
                if not echo and len(mic_raw) >= int(ms["rate"] * ms["ch"] * 2 * 1.0):
                    threading.Thread(target=self._handle_mic_utterance,
                                     args=(mic_raw, ms["rate"], ms["ch"]), daemon=True).start()

            # ---------- pick the output device that is actually playing the call ----------
            energy = {}
            for idx, q in dev_q.items():
                recent = [r for (t, _, r) in list(q)[-50:] if now - t < 1.0]
                energy[idx] = max(recent) if recent else 0.0
            loudest = max(energy, key=energy.get)
            if active is None or (energy[loudest] > 20 and energy.get(active, 0) < 10
                                  and now - active_last_sound > 1.5):
                if active != loudest and energy[loudest] > 20:
                    if active is not None:
                        print(f"[Audio] Call audio moved to: {dev_info[loudest][2]}")
                    active = loudest
                    seg = np.zeros(0, dtype=np.float32)
            if active is None or active not in dev_q:
                active = None
                for q in dev_q.values():
                    q.clear()
                continue

            # ---------- drain new audio ----------
            chunks = []
            max_rms = 0.0
            q = dev_q[active]
            while q:
                t, raw, rms = q.popleft()
                chunks.append(raw)
                if rms > max_rms:
                    max_rms = rms
                if rms > 10:
                    active_last_sound = t
            if chunks:
                self.hud.set_level(max_rms)
            for idx, other in dev_q.items():
                if idx != active:
                    other.clear()
            if chunks:
                rate, ch, _ = dev_info[active]
                audio = self._to_16k_mono(b"".join(chunks), rate, ch)
                if self.dg and self.dg.connected:
                    self.dg.feed_float32(audio)
                    with state_lock:
                        state["speaking"] = max_rms > 15
                        if max_rms > 15:
                            state["last_sound_time"] = now
                    continue
                seg = np.concatenate([seg, audio])
                with self.audio_lock:
                    self.rolling16.append(audio)

            if self.dg and self.dg.connected:
                continue

            # ---------- speech boundaries (every 0.15s for fast turn response) ----------
            if now - last_vad < 0.15 or len(seg) < SR // 2:
                continue
            last_vad = now
            try:
                ts = get_speech_timestamps(seg, vad_opts)
            except Exception as e:
                print(f"[VAD] {e}")
                ts = []

            if not ts:
                if hearing:
                    hearing = False
                    self.hud.set_status("🟢 LISTENING", "#4ECCA3")
                if len(seg) > 3 * SR:
                    seg = seg[-SR // 2:]          # keep a short pre-roll only
                with state_lock:
                    state["speaking"] = False
                continue

            if not hearing:
                hearing = True
                self.hud.set_status("🟡 HEARING SPEECH...", "#FBBF24")
            trailing = len(seg) - ts[-1]["end"]
            with state_lock:
                state["speaking"] = trailing < int(0.3 * SR)
                state["last_sound_time"] = now - trailing / SR

            start = max(0, ts[0]["start"] - int(0.2 * SR))
            span = (ts[-1]["end"] - ts[0]["start"]) / SR
            cut = None
            if trailing >= int(END_PAUSE_SEC * SR):
                cut = ts[-1]["end"]                    # the speaker paused: sentence finished
            elif span >= 25.0:
                gaps = [(ts[i + 1]["start"] - ts[i]["end"], ts[i]["end"]) for i in range(len(ts) - 1)]
                cut = max(gaps)[1] if gaps else ts[-1]["end"]   # very long monologue: split at best pause

            if cut is not None:
                utter = seg[start:min(len(seg), cut + int(0.15 * SR))]
                seg = seg[cut:]
                hearing = False
                speech_sec = sum(t["end"] - t["start"] for t in ts if t["end"] <= cut) / SR
                if speech_sec >= 0.5:
                    threading.Thread(target=self._handle_utterance, args=(utter,), daemon=True).start()
                else:
                    self.hud.set_status("🟢 LISTENING", "#4ECCA3")
            elif now - last_live >= LIVE_TICK_SEC and not self._live_busy and span >= 1.0:
                last_live = now
                self._live_busy = True
                threading.Thread(target=self._live_caption_tick, args=(seg[start:][-8 * SR:].copy(),),
                                 daemon=True).start()

        close_audio()

    # Whisper tends to invent these on silence or noise; never treat them as something you said
    _MIC_HALLUCINATIONS = {"thank you", "thanks for watching", "you", "bye", "okay", "ok", "yeah", "uh", "um", "hmm"}

    def _handle_mic_utterance(self, raw_bytes: bytes, rate: int, channels: int):
        """Transcribes what YOU said so the transcript shows both sides and the AI stays consistent."""
        try:
            text = self._transcribe_bytes(raw_bytes, beam_size=1, rate=rate, channels=channels)
            low = re.sub(r"[^a-z' ]", "", text.lower()).strip()
            if len(text.split()) < 3 or low in self._MIC_HALLUCINATIONS:
                return
            print(f"[You]: {text}")
            self._note_you(text)
        except Exception as e:
            print(f"[Mic] Error: {e}")

    def _normalize_q(self, text: str) -> str:
        import re
        return re.sub(r"\W+", "", text.lower())

    def _extract_latest_question(self, text: str) -> str:
        """Extracts the most recent complete question ending with '?' if present."""
        if "?" not in text:
            return ""
        # Take text up to the last '?' and isolate the last 1-2 sentences
        before_q = text[:text.rfind("?") + 1].strip()
        parts = [p.strip() for p in before_q.split(".") if p.strip()]
        candidate = parts[-1] if parts else before_q
        return candidate if len(candidate.split()) >= 4 else ""

    def _live_caption_tick(self, audio: np.ndarray):
        """Updates live Q: captions WHILE speaking and auto-answers finished '?' questions immediately."""
        try:
            text = self._transcribe_audio(audio, beam_size=1)
            if re.sub(r"[^a-z' ]", "", text.lower()).strip() in _CALL_HALLUCINATIONS:
                return
            if not text:
                return

            # Update live caption bar immediately while speaker is still talking
            self.hud.update_live_caption(text)
            if not self.auto_mode:
                return  # Manual mode: captions only, answer on F7
            if not MID_SPEECH_ANSWER:
                return  # captions only: the answer starts once the speaker pauses (0.55s), never on half a sentence

            # Rule-based question detection (works even when Whisper drops the '?')
            verdict, q_candidate = classify(text)
            # Mid-speech: only fire when the sentence looks finished (ends with ?/./!)
            # otherwise wait for the pause so we never answer half a question.
            if verdict != "question" or not text.rstrip().endswith(("?", ".", "!")):
                q_candidate = ""
            if q_candidate:
                norm = self._normalize_q(q_candidate)
                now = time.time()
                is_dup = (
                    norm and self._last_answered_norm
                    and (norm in self._last_answered_norm or self._last_answered_norm in norm)
                    and (now - self._last_answered_time) < 15.0
                )
                if not is_dup:
                    self._last_answered_norm = norm
                    self._last_answered_time = now
                    print(f"\n[Live Mid-Speech Question]: {q_candidate}")
                    threading.Thread(
                        target=self._stream_to_hud,
                        args=(q_candidate, "verbal", False),
                        daemon=True,
                    ).start()
        except Exception as e:
            print(f"[LiveCaption] Error: {e}")
        finally:
            self._live_busy = False

    def _on_dg_interim(self, text: str):
        """Deepgram Nova-2 live word-by-word interim caption (~150ms latency)."""
        if not text:
            return
        self.hud.set_status("🟡 HEARING SPEECH...", "#FBBF24")
        self.hud.update_live_caption(text)

    def _on_dg_final(self, text: str):
        """Deepgram Nova-2 end-of-utterance callback (0ms post-pause STT wait)."""
        if not text:
            return
        threading.Thread(target=self._process_transcribed_text, args=(text,), daemon=True).start()

    def _handle_utterance(self, audio: np.ndarray):
        """
        Transcribes one finished sentence (cut by Silero VAD) via local CUDA Whisper,
        then passes it into the shared stitching/filtering/answering pipeline.
        """
        try:
            self.hud.set_status("⚡ TRANSCRIBING...", "#60A5FA")
            t0 = time.time()
            text = self._transcribe_audio(audio, beam_size=3)
            print(f"[Whisper] {len(audio) / 16000:.1f}s audio -> {time.time() - t0:.2f}s")
            self._process_transcribed_text(text)
        except Exception as e:
            print(f"[Utterance] Error: {e}")
            self.hud.set_status("🟢 LISTENING", "#4ECCA3")

    def _process_transcribed_text(self, text: str):
        """Shared pipeline for both Deepgram streaming STT and local Whisper."""
        try:
            if re.sub(r"[^a-z' ]", "", (text or "").lower()).strip() in _CALL_HALLUCINATIONS:
                text = ""

            if not text:
                self.hud.set_status("🟢 LISTENING", "#4ECCA3")
                return

            now = time.time()
            # Short 35s window for genuine live follow-ups right after an answer
            has_recent_qa = bool(self.brain.history) and (now - self._last_answered_time) <= 35.0
            followup_hit = has_recent_qa and is_followup(text)

            if not followup_hit and len(text.split()) < 2:
                self.hud.set_status("🟢 LISTENING", "#4ECCA3")
                return

            # Skip if this exact question was just answered live mid-speech
            norm = self._normalize_q(text)
            if (
                not followup_hit
                and self._last_answered_norm
                and (self._last_answered_norm in norm or norm in self._last_answered_norm)
                and (now - self._last_answered_time) < 12.0
            ):
                self.hud.set_status("🟢 LISTENING", "#4ECCA3")
                return

            # Stitch onto previous unfinished fragment if spoken within 10 seconds
            # (a finished question ending in "?" is never glued onto the next one)
            if (not followup_hit and self.pending_fragment and (now - self.pending_time) <= 10.0
                    and not self.pending_fragment.rstrip().endswith("?")):
                combined = f"{self.pending_fragment} {text}".strip()
            else:
                combined = text

            print(f"\n[Heard{' (Follow-up)' if followup_hit else ''}]: {combined}")
            self._note_heard(text)
            if not self.auto_mode:
                self.hud.update_live_caption(combined)
                self.hud.set_status("🟢 LISTENING", "#4ECCA3")
                return

            if followup_hit:
                # Let Claude/LLM confirm it's a real follow-up (force=False so messy fragments still silently SKIP)
                fu_prompt = (
                    f"[Possible Interviewer Follow-Up — if this is incomplete noise or casual talk, reply ONLY: SKIP. "
                    f"Otherwise resolve 'it/that/this' using the previous Q&A and answer ONLY this follow-up directly.]\n"
                    f"{combined}"
                )
                answered = self._stream_to_hud(
                    fu_prompt, mode="verbal", force=False, display_label=f"↩ Follow-up: {combined}"
                )
                if answered:
                    self._last_answered_norm = self._normalize_q(combined)
                    self._last_answered_time = now
                    self.pending_fragment = ""
                    self.pending_time = 0.0
                return
            verdict, q_text = classify(combined)
            if verdict == "chatter":
                print("[Filter] chatter, ignored")
                self.hud.set_status("🟢 LISTENING", "#4ECCA3")
                return
            if verdict == "unsure":
                # Don't drop phrasing because of a hardcoded list: send any real sentence (>=4 words)
                # straight to the LLM with our silent SKIP gate so the AI itself decides.
                if len(combined.split()) < 4:
                    self.pending_fragment = text[-220:]
                    self.pending_time = now
                    self.hud.set_status("🟢 LISTENING", "#4ECCA3")
                    return
                answered = self._stream_to_hud(combined, mode="verbal", force=False)
                if answered:
                    self._last_answered_norm = self._normalize_q(combined)
                    self._last_answered_time = now
                    self.pending_fragment = ""
                    self.pending_time = 0.0
                else:
                    self.pending_fragment = text[-220:]
                    self.pending_time = now
                return
            if verdict == "question":
                combined = q_text
                # only hold true 2-3 word stubs ("Can you explain") with no '?';
                # 4-5 word viva questions ("Explain about Cloud DNS.") should answer immediately
                if len(q_text.split()) < 4 and not combined.rstrip().endswith("?"):
                    print(f"[Held fragment]: {combined}")
                    self.pending_fragment = combined[-220:]
                    self.pending_time = now
                    self.hud.set_status("🟢 LISTENING", "#4ECCA3")
                    return
            answered = self._stream_to_hud(combined, mode="verbal", force=False)

            if answered:
                self._last_answered_norm = self._normalize_q(combined)
                self._last_answered_time = now
                self.pending_fragment = ""
                self.pending_time = 0.0
            else:
                # Hold unfinished thought in case he finishes the question after pausing
                self.pending_fragment = combined[-220:]
                self.pending_time = now
        except Exception as e:
            print(f"[Utterance] Error: {e}")
            self.hud.set_status("🟢 LISTENING", "#4ECCA3")

    def run(self):
        threading.Thread(target=self._warmup_ollama, daemon=True).start()
        listener = threading.Thread(target=self._audio_listener_worker, daemon=True)
        listener.start()
        self.hud.mainloop()


def main():
    copilot = CueCopilotApp()
    copilot.run()


if __name__ == "__main__":
    main()

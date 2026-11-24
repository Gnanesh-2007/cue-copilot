"""
AI Brain Engine - Beast Mode V2
- Real-time Token Streaming (~250ms Time-To-First-Token)
- Sliding Conversation Memory (understands follow-up questions)
- Automatic Local RAG Injection (pulls from your context/ PDFs & code)
- Auto-detects best Ollama model on your RTX 3050 (prefers 7B/8B models, falls back to 3B)
- Supports both Verbal Viva/Interview mode and Screen-Snap Coding/OCR mode
"""
import os
import re
import requests
import json
import threading
from collections import deque

# 127.0.0.1 instead of "localhost": on Windows "localhost" tries IPv6 first and wastes ~2s per request
OLLAMA_BASE = "http://127.0.0.1:11434"
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

CLOUD_DEFAULTS = {
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o",
}


def load_cloud_config() -> tuple[str, str, str]:
    """
    Returns (provider, api_key, model_name) from settings.json or environment variables.
    provider: 'anthropic' | 'gemini' | 'openai' | 'ollama'
    """
    cfg = {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        pass

    provider = (cfg.get("cloud_provider") or "").strip().lower()
    api_key = (cfg.get("cloud_api_key") or "").strip()
    model = (cfg.get("cloud_model") or "").strip()

    # Auto-detect from specific keys or env vars if cloud_provider wasn't explicitly set
    if not api_key:
        for prov, key_name, env_name in (
            ("anthropic", "anthropic_key", "ANTHROPIC_API_KEY"),
            ("gemini", "gemini_key", "GEMINI_API_KEY"),
            ("openai", "openai_key", "OPENAI_API_KEY"),
        ):
            val = (cfg.get(key_name) or os.environ.get(env_name) or "").strip()
            if val and (not provider or provider == prov):
                provider = prov
                api_key = val
                break

    if provider in CLOUD_DEFAULTS and api_key:
        if not model and provider == "anthropic":
            model = _detect_latest_anthropic_sonnet(api_key)
        return provider, api_key, model or CLOUD_DEFAULTS[provider]
    return "ollama", "", ""


def _detect_latest_anthropic_sonnet(api_key: str) -> str:
    """Queries Anthropic /v1/models once to auto-pick the latest available Sonnet model."""
    try:
        r = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=4,
        )
        if r.status_code == 200:
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            sonnets = [i for i in ids if "sonnet" in i.lower()]
            if sonnets:
                return sonnets[0]
            if ids:
                return ids[0]
    except Exception:
        pass
    return CLOUD_DEFAULTS["anthropic"]

# Priority order of fast local models (3B first for instant speed & zero lag)
PREFERRED_MODELS = [
    "qwen2.5:3b",
    "llama3.2:3b",
    "qwen2.5-coder:3b",
    "qwen2.5:7b",
    "qwen2.5-coder:7b",
    "llama3.1:8b",
    "mistral:7b",
    "phi3.5",
]

VERBAL_SYSTEM_PROMPT = """You are a real-time interview copilot answering AS THE CANDIDATE (in first person when asked personal/behavioral questions, or direct technical explanations for technical questions).

Rules:
1. If the input is ONLY greetings, small talk, logistics, or NOT a question to answer (e.g. "thank you", "can you hear me", "wait a second", "welcome"), reply with ONLY the single word: SKIP
   Personal / HR questions ("tell me about yourself", "introduce yourself", "your strengths", "why this company", "where do you see yourself") are real questions: ALWAYS answer them, never SKIP.
   Confirmation questions about the candidate ("you are from X branch, right?", "you know Java, right?") are real questions too: answer "Yes, ..." and add one useful line, never SKIP.
2. Otherwise, ignore any warm-up filler ("be relaxed", "okay my first question") and answer ONLY the actual question clearly and naturally.
   CRITICAL: Treat every new standalone question as a completely fresh topic. NEVER reference, repeat, or tie back to previous Q&As unless the new question explicitly refers to them (e.g. "why?", "what about that?", "how does it scale?").
3. Never ask "How can I assist you?". You are giving the candidate the exact words to say.
4. NEVER invent personal facts (years of experience, employers, internships, hackathons, open-source work, project names, numbers, certifications). Use only facts stated in the question itself, in [Interview setup] (resume, job description, notes), in [Recent conversation on the call], in [What the candidate already said], or in [Candidate Project / Report Reference]. When a resume is given, always answer with its real projects, skills and education. Facts the interviewer states about the candidate in the question are true and must be used.
   For projects, internships or experience that are NOT in those facts, never make up names or apps: use placeholders like [your project name] and [what it does]; you may build on skills already mentioned.
   Those bracketed blocks are input only: never write any [ ... ] block or "Already said" note in your reply. If there is no reference for a personal question, give a ready-to-speak first-person answer with short placeholders in square brackets for the facts, e.g. "I'm a final-year [your branch] student at [your college]..."
5. Write the first answer sentence as natural speech the candidate can read aloud immediately (confident, no filler, no "As an AI").
6. STRICT ADAPTIVE FORMAT (plain text, NO ### or ** markdown, NEVER write long paragraphs):
   Pick the exact layout that matches the question type — every bullet MUST be on its own new line starting with "• " and stay short & scannable (max 15-20 words per bullet):

   - CONCEPT / THEORY ("What is X?", "Explain Y"):
     DIRECT ANSWER
     <1 crisp sentence the candidate can say immediately>

     KEY POINTS
     • <core mechanism / how it works>
     • <key benefit or use case>
     • <important detail or best practice>

   - COMPARISON ("Difference between X and Y", "X vs Y"):
     DIRECT ANSWER
     <1 sentence stating the main difference>

     KEY POINTS
     • X: <concise role & traits>
     • Y: <concise role & traits>
     • When to use: <quick rule of thumb>

   - SCENARIO / ARCHITECTURE ("How would you design/handle...", "Which service for..."):
     DIRECT ANSWER
     <1 sentence naming the exact recommended service or solution>

     KEY POINTS
     • Why it fits: <reason>
     • Setup / Steps: <exact config, role, or command>
     • Trade-off / Alternative: <why other options are worse>

   - CODING ("Write a program", "Implement", "Code for..."):
     APPROACH
     <1-2 short lines naming the OPTIMAL algorithm (never brute force) + exact Time & Space complexity>

     CODE
     ```<language>
     <optimal, production-clean code handling all edge cases (empty/null, duplicates, overflow) with brief 3-5 word inline comments on tricky lines>
     ```

     KEY POINTS
     • Core trick: <1 short line on why this is optimal>
     • Edge cases: <1 short line on handled edge cases>

   - PERSONAL / HR / BEHAVIORAL ("Tell me about yourself", "Challenge you faced"):
     DIRECT ANSWER
     <1 confident first-person opening sentence>

     KEY POINTS
     • Situation / Background: <short point>
     • Action / Skills: <short point>
     • Result / Goal: <short point>
"""

LOCAL_VERBAL_PROMPT = """You are a fast, concise interview copilot.
If the input is ONLY small talk/greetings/not a question, reply ONLY: SKIP
Never use ### or ** markdown. Never write long paragraphs.

For theory/concepts/HR, ALWAYS use this exact format:
DIRECT ANSWER
<1 clear, confident sentence>

KEY POINTS
• <short point 1>
• <short point 2>
• <short point 3>

For coding questions, ALWAYS use this exact format:
APPROACH
<1 sentence optimal algorithm + Time & Space complexity>

CODE
```<language>
<clean optimal code>
```

KEY POINTS
• <1-line logic>
• <1-line edge case>
"""

PREP_SYSTEM_PROMPT = """You are an interview coach. From the interview setup below (company, role, round, job description, resume, notes), predict the questions this candidate is most likely to be asked, and give a short model answer for each, written in first person as the candidate, using ONLY facts from the setup (use [placeholders] for anything missing).

Output exactly these three sections, plain text (no ** or #):
HR QUESTIONS
• <question>
<2-3 sentence answer>

TECHNICAL QUESTIONS
• <question>
<2-3 sentence answer>

PROJECT QUESTIONS
• <question>
<2-3 sentence answer>

Give 3 questions in each section. EVERY question is immediately followed by its answer on the next line (never list the questions first and answer later, never repeat a section).
Technical questions must come from the skills in the job description and resume. Project questions must name the candidate's real projects from the resume.

STRICT: never add tools, services, numbers, bugs or stories that are not written in the setup. If a good answer needs a detail the setup does not give, write it as [fill in: what detail] instead of guessing.

Example of the exact shape:
HR QUESTIONS
• Why do you want to join this company?
I want to join because the role matches my interest in backend development, and I want to grow with a team that works on large real-world systems.
"""

PREP_VIVA_PROMPT = """You are a viva coach. A faculty panel will question this student about their OWN projects (from the resume / setup below). Predict the questions the panel is most likely to ask, and give a short model answer for each in first person as the student, using ONLY facts from the setup.

Output exactly these three sections, plain text (no ** or #):
PROJECT QUESTIONS
• <question about one of the student's real projects: what it does, architecture, a specific feature, a challenge, testing, limitations, future scope>
<2-3 sentence answer>

TECHNICAL QUESTIONS
• <why this technology / how this part works internally, for a technology the student actually used>
<2-3 sentence answer>

THEORY QUESTIONS
• <core CS concept behind the student's work: e.g. REST, JWT, caching, database indexing, OOP, OS, networking>
<2-3 sentence answer>

Give 4 project questions, 3 technical questions and 3 theory questions. EVERY question is immediately followed by its answer on the next line; never repeat a section. No HR questions (no "why join", no career goals).
STRICT: never add tools, numbers or stories that are not written in the setup; if an answer needs a missing detail, write [fill in: what detail].
"""

PREP_TOPIC_PROMPT = """You are an interview / viva coach. The candidate has NOT given a resume or project details, only the topic of the interview or viva (below). Predict the questions most likely to be asked about THAT TOPIC, and give a short, correct model answer for each.

Output exactly these two sections, plain text (no ** or #):
CORE QUESTIONS
• <a fundamental question about the topic>
<2-3 sentence answer>

SCENARIO QUESTIONS
• <a practical "how would you / what would you use" question about the topic>
<2-3 sentence answer>

Give 6 core questions and 4 scenario questions. EVERY question is immediately followed by its answer on the next line; never repeat a section.
STRICT: never invent a project, app, company or personal experience for the candidate - there is none in the setup. If the candidate would need a personal example, write [your example].
"""

FORCE_SYSTEM_PROMPT = """You are a real-time interview copilot. The candidate pressed a button because the interviewer just asked something or just finished explaining a problem.
Below is the transcript of the last minute of the live interview (speaker labels can be wrong; ignore chit-chat like screen sharing, "can you hear me", small talk).

Find what the interviewer wants RIGHT NOW (usually the problem or question described most recently, often spread over several lines) and answer it as the candidate. Never reply SKIP.

If it is a CODING or DESIGN problem, use ONLY these two headings:
APPROACH
<restate the requirements as a short checklist, then the design / algorithm and its time & space complexity>

CODE
<clean working code (Python unless another language was mentioned) that covers EVERY requirement that was mentioned>
Before writing the code, check each requirement against it: no method may undo or break another requirement. End the reply right after the code (no extra sections).

Otherwise (theory, HR, behavioural), use ONLY these two headings:
DIRECT ANSWER
<1-2 sentences the candidate can say aloud>

KEY POINTS
• <2-4 short bullets>

Rules: plain text (no ** or #), no filler, never invent personal facts (projects, companies, numbers): use [placeholders] instead.
"""

SCREEN_SYSTEM_PROMPT = """You are an elite real-time technical & coding interview copilot analyzing a screen capture (OCR text).
Identify the core question, MCQ, coding problem, or technical slide from the noisy OCR text (ignore browser tabs, URLs, menus, and UI noise).

If it is a THEORY or MCQ question, use ONLY these two headings:
DIRECT ANSWER
<exact option or 1-2 crisp, confident sentences>

KEY POINTS
• <3-4 accurate technical bullets explaining why, specific mechanisms, and production trade-offs>

If it is a CODING problem, use ONLY these three headings:
APPROACH
<optimal algorithm (never brute force) + exact Time & Space complexity in 2 short lines>

CODE
```<language>
<optimal working code matching the EXACT class/function signature, parameter types, and language shown on screen; handle all edge cases (empty, duplicates, overflow) with brief 3-5 word inline comments on tricky lines>
```

KEY POINTS
• Core trick: <why this achieves optimal complexity>
• Edge cases: <key edge cases handled>

If it is an ERROR message, stack trace or broken code, use ONLY these two headings:
WHAT THIS SHOWS
<one sentence: what is wrong and why>

FIX
<the corrected code or the exact steps>

Strict Rules:
- Plain text only: NEVER use markdown syntax like ### or **.
- NEVER repeat instructions in parentheses.
- Zero filler. Jump straight to the answer.
"""


SCREEN_VISION_PROMPT = """You are a real-time technical interview copilot looking at a SCREENSHOT (a diagram, chart, slide, UI or image-heavy screen). Noisy OCR text from the same screen is also given as a hint; trust the image over the OCR.

If the screen asks a question, reply in exactly this layout:
DIRECT ANSWER
<the answer in 1-2 sentences>

KEY POINTS
• <short bullet>
• <short bullet>

Otherwise (a diagram, architecture, chart or slide), reply in exactly this layout:
WHAT THIS SHOWS
<1-2 plain sentences describing it>

KEY POINTS
• <main components or steps and how data flows>
• <a bottleneck, risk or trade-off>

Rules: plain text only (no ** and no #), each bullet on its own line starting with "• ", never invent text that is not visible.
"""


def classify_screen(text: str) -> str:
    """Decides which model reads the screen: 'mcq' | 'code' | 'error' | 'theory' use OCR + the strong text
    model; 'diagram' (little text / no question) goes to the vision model."""
    t = (text or "").strip()
    low = t.lower()
    if len(t) < 60:
        return "diagram"
    if len(re.findall(r"(?m)^\s*\(?[A-Da-d][\).:]\s+\S", t)) >= 3:
        return "mcq"
    if any(k in low for k in ("traceback", "exception", "error:", "typeerror", "syntaxerror",
                              "undefined", "segmentation fault", "nullpointer")):
        return "error"
    kws = ("def ", "function", "class ", "return", "public static", "int main", "input:", "output:",
           "example", "nums", "array", "string", "linked list", "implement", "write a", "complexity",
           "leetcode", "sql", "select ")
    if sum(k in low for k in kws) >= 2:
        return "code"
    if "?" in t or re.search(r"\b(what|why|how|explain|define|difference|which|describe)\b", low):
        return "theory"
    return "diagram"

# Models that can read images (checked in this order); never used as the text brain.
VISION_PREFERRED = ["qwen2.5vl:7b", "qwen2.5vl:3b", "qwen2.5vl", "gemma3:4b", "minicpm-v", "llama3.2-vision", "llava", "moondream"]
_VISION_MARKERS = ("vl", "vision", "llava", "moondream", "minicpm-v", "gemma3")


def _is_vision_name(name: str) -> bool:
    n = name.lower()
    return any(m in n for m in _VISION_MARKERS)


def detect_vision_model():
    """Returns the best installed image-capable model, or None (F9 then falls back to OCR only)."""
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=2)
        if r.status_code == 200:
            installed = [m["name"] for m in r.json().get("models", [])]
            for pref in VISION_PREFERRED:
                for inst in installed:
                    if inst == pref or inst.startswith(pref):
                        return inst
    except Exception:
        pass
    return None


def detect_best_model() -> str:
    """Auto-detects the strongest TEXT model currently pulled in Ollama."""
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=2)
        if r.status_code == 200:
            installed = [m["name"] for m in r.json().get("models", []) if not _is_vision_name(m["name"])]
            if installed:
                for pref in PREFERRED_MODELS:
                    for inst in installed:
                        if inst == pref or inst.startswith(pref):
                            return inst
                return installed[0]
    except Exception:
        pass
    return "qwen2.5:7b"


_PERSONAL_Q = re.compile(
    r"\b(yourself|introduce|background|resume|cv|your project|your projects|projects you|internship|"
    r"your experience|worked on|you built|you developed|you created|your role|your team|strengths?|"
    r"weakness(?:es)?|challenge you|conflict|why this company|why should we|where do you see|"
    r"career goal|proud of|your skill|your tech stack|tell me about a time|hobby|hobbies|cgpa|college|branch)\b"
)


def _needs_resume(question_text: str, mode: str) -> bool:
    """Only inject the full resume when the question actually asks about the candidate/projects/HR."""
    if mode == "prep":
        return True
    if mode == "screen":
        return False
    return bool(_PERSONAL_Q.search((question_text or "").lower()))


class CopilotBrain:
    def __init__(self, rag=None, model: str | None = None):
        self.rag = rag
        self.cloud_provider, self.cloud_key, self.cloud_model = load_cloud_config()
        self.cloud_enabled = self.cloud_provider != "ollama"
        self.local_model = model or detect_best_model()
        self.local_vision = detect_vision_model()
        self.model = self.cloud_model if self.cloud_enabled else self.local_model
        self.vision_model = self.cloud_model if self.cloud_enabled else self.local_vision
        self.history: deque[dict] = deque(maxlen=2)  # Sliding memory of last 2 Q&As (keeps new topics fresh)
        self.said: deque[str] = deque(maxlen=4)      # what the candidate (mic) already said on this call
        self.profile_block = ""                      # Interview setup (resume, JD, company, round) from the HUD
        self.has_resume = False
        self.round = "Mixed"
        self.has_notes = False
        self.last_sources: list[str] = []            # filenames matched by Hybrid RAG on the last question
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()

    def refresh_model(self):
        self.cloud_provider, self.cloud_key, self.cloud_model = load_cloud_config()
        self.local_model = detect_best_model()
        self.local_vision = detect_vision_model()
        self.cloud_enabled = self.cloud_provider != "ollama"
        self.model = self.cloud_model if self.cloud_enabled else self.local_model
        self.vision_model = self.cloud_model if self.cloud_enabled else self.local_vision
        return self.model

    def toggle_backend(self) -> tuple[str, str | None, bool]:
        """Toggles between Frontier Cloud (if key configured) and Local Ollama (₹0)."""
        if self.cloud_provider == "ollama" or not self.cloud_key:
            return self.model, self.vision_model, False
        self.cloud_enabled = not self.cloud_enabled
        self.model = self.cloud_model if self.cloud_enabled else self.local_model
        self.vision_model = self.cloud_model if self.cloud_enabled else self.local_vision
        return self.model, self.vision_model, self.cloud_enabled

    def list_available_models(self) -> tuple[list[dict], str]:
        """Returns (list_of_model_options, active_model_id) for the HUD dropdown."""
        opts = []
        if self.cloud_provider != "ollama" and self.cloud_key:
            opts.append({
                "id": f"cloud:{self.cloud_provider}",
                "label": f"☁ {self.cloud_model}",
            })
        ollama_added = False
        try:
            r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=2)
            if r.status_code == 200:
                installed = [m["name"] for m in r.json().get("models", []) if not _is_vision_name(m["name"])]
                for name in installed:
                    opts.append({"id": f"ollama:{name}", "label": f"⚡ {name} (Local)"})
                    ollama_added = True
        except Exception:
            pass
        if not ollama_added:
            for fallback_name in ("qwen2.5:3b", "qwen2.5:7b"):
                opts.append({"id": f"ollama:{fallback_name}", "label": f"⚡ {fallback_name} (Local)"})

        active_id = f"cloud:{self.cloud_provider}" if self.cloud_enabled else f"ollama:{self.local_model}"
        return opts, active_id

    def select_model(self, model_id: str) -> tuple[str, str | None, bool]:
        """Switches active brain to the chosen dropdown item ('cloud:anthropic' or 'ollama:qwen2.5:3b')."""
        if model_id.startswith("cloud:") and self.cloud_key:
            self.cloud_enabled = True
            self.model = self.cloud_model
            self.vision_model = self.cloud_model
            return self.model, self.vision_model, True
        if model_id.startswith("ollama:"):
            chosen = model_id.split("ollama:", 1)[1].strip()
            if chosen:
                self.local_model = chosen
            self.cloud_enabled = False
            self.model = self.local_model
            self.vision_model = self.local_vision
            return self.model, self.vision_model, False
        return self.model, self.vision_model, self.cloud_enabled

    def clear_memory(self):
        self.history.clear()
        self.said.clear()

    def note_candidate_said(self, text: str):
        """Remember what the candidate actually said so later answers stay consistent with it."""
        text = (text or "").strip()
        if text:
            self.said.append(text)

    def cancel_current(self):
        self._cancel_event.set()

    def stream_answer(self, question_text: str, mode: str = "verbal", interrupt: bool = False, images=None,
                      context: str = ""):
        """
        Generator that streams tokens live from Ollama.
        Yields string chunks as soon as they arrive (~250ms TTFT).
        Only cancels an in-flight answer when interrupt=True (F7/F9).
        images: optional list of base64 JPEGs; used only when a vision model is installed.
        """
        if interrupt:
            self._cancel_event.set()
        with self._lock:
            self._cancel_event.clear()

            use_vision = bool(images and self.vision_model)
            model_name = self.vision_model if use_vision else self.model

            # Build messages with sliding conversation memory
            if use_vision:
                system_prompt = SCREEN_VISION_PROMPT
            elif mode == "force":
                system_prompt = FORCE_SYSTEM_PROMPT
            elif mode == "prep":
                if not (self.has_resume or self.has_notes):
                    system_prompt = PREP_TOPIC_PROMPT        # nothing personal known: never invent projects
                else:
                    system_prompt = PREP_VIVA_PROMPT if self.round == "Viva" else PREP_SYSTEM_PROMPT
            else:
                system_prompt = SCREEN_SYSTEM_PROMPT if mode == "screen" else VERBAL_SYSTEM_PROMPT
            messages = [{"role": "system", "content": system_prompt}]

            # Add recent conversation memory for verbal follow-ups
            if mode == "verbal" and self.history:
                for turn in self.history:
                    messages.append({"role": "user", "content": turn["q"]})
                    messages.append({"role": "assistant", "content": turn["a"]})

            # Retrieve relevant snippets from user's context/ folder (Hybrid RAG: docs + general AI knowledge)
            rag_context = ""
            self.last_sources = []
            if self.rag and mode != "screen":   # screen questions are about the screen, not the candidate
                if hasattr(self.rag, "retrieve_with_sources"):
                    rag_context, self.last_sources = self.rag.retrieve_with_sources(question_text, top_k=3)
                else:
                    rag_context = self.rag.retrieve(question_text, top_k=3)

            if rag_context:
                user_content = (
                    f"[Uploaded Document Reference — Hybrid Mode: prioritize these exact facts when relevant, "
                    f"and combine with your general technical knowledge for a complete answer]\n{rag_context}\n\n"
                    f"[Current Question]\n{question_text}"
                )
            else:
                user_content = question_text

            if (mode == "verbal" and not rag_context and not self.has_resume and
                    re.search(r"\b(project|projects|internship|experience|worked on|hackathon|built|developed)\b",
                              question_text.lower())):
                user_content += ("\n\n[Note: the candidate's real projects/internships are unknown. Do not name any "
                                 "project, app or company; write [your project name], [what it does], [tech used] "
                                 "as placeholders the candidate fills in.]")

            if mode == "verbal" and context:
                user_content = f"[Recent conversation on the call, for context only]\n{context}\n\n{user_content}"

            if mode == "verbal" and self.said:
                said_block = "\n".join(f"- {s[:300]}" for s in self.said)
                user_content = (
                    f"[What the candidate already said]\n"
                    f"{said_block}\n\n{user_content}"
                )

            if mode in ("verbal", "force", "prep") and self.profile_block:
                if _needs_resume(question_text, mode):
                    user_content = f"{self.profile_block}\n\n{user_content}"
                else:
                    # Pure technical/coding question: pass only compact subject/round header (no resume noise)
                    header_lines = [
                        ln for ln in self.profile_block.splitlines()[:4]
                        if not ln.startswith("Candidate resume:")
                    ]
                    if header_lines:
                        user_content = "\n".join(header_lines) + "\n\n" + user_content

            user_msg = {"role": "user", "content": user_content}
            if use_vision:
                user_msg["images"] = list(images)
            messages.append(user_msg)

            max_tokens = 1100 if use_vision else {"force": 1400, "prep": 1800}.get(mode, 900)
            temp = 0.3 if use_vision else 0.35

            full_response = []

            # ---- Frontier Cloud Brain (Anthropic Claude / Google Gemini / OpenAI GPT-4o) ----
            if self.cloud_enabled and self.cloud_provider in ("anthropic", "gemini", "openai") and self.cloud_key:
                try:
                    if self.cloud_provider == "anthropic":
                        gen = self._stream_anthropic(system_prompt, messages[1:], max_tokens, temp)
                    elif self.cloud_provider == "gemini":
                        gen = self._stream_gemini(system_prompt, messages[1:], max_tokens, temp)
                    else:
                        gen = self._stream_openai(system_prompt, messages[1:], max_tokens, temp)

                    for token in gen:
                        if self._cancel_event.is_set():
                            return
                        full_response.append(token)
                        yield token

                    answer_str = "".join(full_response).strip()
                    if answer_str and not answer_str.upper().startswith(("SKIP", "[SKIP")) and mode == "verbal":
                        self.history.append({"q": question_text, "a": answer_str})
                    return
                except Exception as e:
                    print(f"[Cloud/{self.cloud_provider}] Error ({e}); falling back to local Ollama {self.local_model}")
                    model_name = self.local_vision if (use_vision and self.local_vision) else self.local_model
                    if use_vision and not self.local_vision:
                        user_msg.pop("images", None)
                        use_vision = False
                    full_response.clear()

            # Local Ollama: use razor-short prompt + 2048 ctx for instant TTFT and crisp Claude-style formatting
            local_messages = list(messages)
            if mode == "verbal" and not use_vision:
                local_messages[0] = {"role": "system", "content": LOCAL_VERBAL_PROMPT}

            payload = {
                "model": model_name,
                "messages": local_messages,
                "stream": True,
                "keep_alive": "30m",
                "options": {
                    "temperature": 0.2,
                    "num_predict": 480 if mode == "verbal" else max_tokens,
                    "num_ctx": 4096 if (use_vision or mode in ("prep", "force")) else 2048,
                }
            }

            try:
                with requests.post(
                    f"{OLLAMA_BASE}/api/chat",
                    json=payload,
                    stream=True,
                    timeout=45
                ) as resp:
                    if resp.status_code != 200:
                        yield (
                            f"⚠️ Ollama returned status {resp.status_code} for model '{model_name}'.\n"
                            f"Run: ollama pull {model_name}"
                        )
                        return

                    for line in resp.iter_lines(decode_unicode=True):
                        if self._cancel_event.is_set():
                            return
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            token = data.get("message", {}).get("content", "")
                            if token:
                                full_response.append(token)
                                yield token
                            if data.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue

                # Save to sliding memory (only if not skipped)
                answer_str = "".join(full_response).strip()
                if answer_str and not answer_str.upper().startswith(("SKIP", "[SKIP")) and mode == "verbal":
                    self.history.append({"q": question_text, "a": answer_str})

            except requests.exceptions.ConnectionError:
                yield (
                    "⚠️ Ollama is not running!\n\n"
                    "1. Install Ollama for Windows (if not installed).\n"
                    "2. Open terminal once and run:\n"
                    "   ollama pull qwen2.5:7b\n"
                    "   (or ollama pull llama3.2:3b for lighter speed)\n"
                    "3. Ollama will run silently in your system tray."
                )
            except Exception as e:
                yield f"⚠️ Generation error: {e}"

    def _stream_anthropic(self, system_prompt: str, turns: list[dict], max_tokens: int, temp: float):
        """Streams Anthropic Claude Messages API (text + vision screenshots)."""
        api_msgs = []
        for m in turns:
            imgs = m.get("images") or []
            if imgs:
                blocks = [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}
                    for b64 in imgs
                ]
                blocks.append({"type": "text", "text": m["content"]})
                api_msgs.append({"role": m["role"], "content": blocks})
            else:
                api_msgs.append({"role": m["role"], "content": m["content"]})

        headers = {
            "x-api-key": self.cloud_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": self.cloud_model,
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": api_msgs,
            "max_tokens": max_tokens,
            "stream": True,
        }
        with requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, stream=True, timeout=30) as r:
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                    if ev.get("type") == "content_block_delta":
                        tok = ev.get("delta", {}).get("text", "")
                        if tok:
                            yield tok
                except json.JSONDecodeError:
                    continue

    def _stream_gemini(self, system_prompt: str, turns: list[dict], max_tokens: int, temp: float):
        """Streams Google Gemini API (text + vision screenshots)."""
        contents = []
        for m in turns:
            role = "model" if m["role"] == "assistant" else "user"
            parts = []
            for b64 in (m.get("images") or []):
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
            parts.append({"text": m["content"]})
            contents.append({"role": role, "parts": parts})

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.cloud_model}"
            f":streamGenerateContent?alt=sse&key={self.cloud_key}"
        )
        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {"temperature": temp, "maxOutputTokens": max_tokens},
        }
        with requests.post(url, json=body, stream=True, timeout=30) as r:
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                    cands = ev.get("candidates") or []
                    if cands:
                        for p in (cands[0].get("content", {}).get("parts") or []):
                            tok = p.get("text", "")
                            if tok:
                                yield tok
                except json.JSONDecodeError:
                    continue

    def _stream_openai(self, system_prompt: str, turns: list[dict], max_tokens: int, temp: float):
        """Streams OpenAI Chat Completions API (text + vision screenshots)."""
        api_msgs = [{"role": "system", "content": system_prompt}]
        for m in turns:
            imgs = m.get("images") or []
            if imgs:
                blocks = [{"type": "text", "text": m["content"]}]
                for b64 in imgs:
                    blocks.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                api_msgs.append({"role": m["role"], "content": blocks})
            else:
                api_msgs.append({"role": m["role"], "content": m["content"]})

        headers = {"Authorization": f"Bearer {self.cloud_key}", "Content-Type": "application/json"}
        body = {
            "model": self.cloud_model,
            "messages": api_msgs,
            "max_tokens": max_tokens,
            "temperature": temp,
            "stream": True,
        }
        with requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body, stream=True, timeout=30) as r:
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                    choices = ev.get("choices") or []
                    if choices:
                        tok = choices[0].get("delta", {}).get("content", "")
                        if tok:
                            yield tok
                except json.JSONDecodeError:
                    continue


if __name__ == "__main__":
    brain = CopilotBrain()
    print(f"Detected model: {brain.model}")
    for chunk in brain.stream_answer("Explain ACID properties in DBMS."):
        print(chunk, end="", flush=True)
    print()

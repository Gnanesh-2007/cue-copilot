"""
Interview Setup (profile): resume + job description + company/role/round + notes.
Filled in from the HUD's Setup tab, saved to profile.json, and fed into every answer.
"""
import json
import os
import re
from collections import Counter

from context_loader import _read_pdf, _read_docx

PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.json")
ROUNDS = ["Mixed", "Viva", "HR", "Technical", "Coding", "Managerial"]
FIELDS = ("company", "role", "round", "jd", "notes", "resume_name", "resume_text")

ROUND_STYLE = {
    "Viva": ("This is a college project viva / review by faculty, not a job interview: questions are about the "
             "student's OWN projects (what it does, architecture, why each technology was chosen, how a feature "
             "works internally, challenges, testing, limitations, future scope) and the CS theory behind them. "
             "Answer as the student who built it: clear, confident, simple words, no HR talk about joining a company."),
    "HR": "This is an HR round: warm, confident, story-based answers (situation -> action -> result), no code.",
    "Technical": "This is a technical round: precise definitions, one real-world example, mention trade-offs.",
    "Coding": "This is a coding round: lead with the approach and complexity, give clean code when asked to solve.",
    "Managerial": "This is a managerial round: teamwork, ownership, conflict handling, priorities, impact on users.",
    "Mixed": "",
}


def empty_profile() -> dict:
    return {"company": "", "role": "", "round": "Mixed", "jd": "", "notes": "", "resume_name": "", "resume_text": ""}


def load_profile() -> dict:
    p = empty_profile()
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        p.update({k: data.get(k, p[k]) for k in FIELDS})
    except Exception:
        pass
    if p["round"] not in ROUNDS:
        p["round"] = "Mixed"
    return p


def save_profile(p: dict):
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump({k: p.get(k, "") for k in FIELDS}, f, indent=2, ensure_ascii=False)


def read_resume(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        text = _read_pdf(path)
    elif ext == ".docx":
        text = _read_docx(path)
    else:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def is_filled(p: dict) -> bool:
    return any((p.get(k) or "").strip() for k in ("company", "role", "jd", "notes", "resume_text"))


def summary(p: dict) -> str:
    """Short text for the HUD chip, e.g. 'Google · SDE Intern'."""
    parts = [x for x in ((p.get("company") or "").strip(), (p.get("role") or "").strip()) if x]
    return " · ".join(parts) or ("Resume loaded" if p.get("resume_text") else "")


# Common certification / exam short forms, so the model never guesses ("ACE framework")
_ABBREV = [
    (r"\bACE\b", "Associate Cloud Engineer (ACE, Google Cloud certification)"),
    (r"\bPCA\b", "Professional Cloud Architect (PCA, Google Cloud certification)"),
    (r"\bPCD\b", "Professional Cloud Developer (PCD, Google Cloud certification)"),
    (r"\bPDE\b", "Professional Data Engineer (PDE, Google Cloud certification)"),
    (r"\bCDL\b", "Cloud Digital Leader (CDL, Google Cloud certification)"),
    (r"\bSAA(?:-C0\d)?\b", "AWS Solutions Architect Associate (SAA certification)"),
    (r"\bCLF(?:-C0\d)?\b", "AWS Cloud Practitioner (CLF certification)"),
    (r"\bAZ-900\b", "Microsoft Azure Fundamentals (AZ-900)"),
    (r"\bAZ-104\b", "Microsoft Azure Administrator (AZ-104)"),
    (r"\bCCNA\b", "Cisco Certified Network Associate (CCNA)"),
    (r"\bCKA\b", "Certified Kubernetes Administrator (CKA)"),
    (r"\bOCA\b", "Oracle Certified Associate (OCA, Java)"),
]


def expand_abbrev(text: str) -> str:
    for pat, full in _ABBREV:
        if re.search(pat, text) and full.split(" (")[0].lower() not in text.lower():
            text = re.sub(pat, full, text, count=1)
    return text


def prompt_block(p: dict, resume_chars: int = 2600, jd_chars: int = 1500) -> str:
    """The block every answer sees. Trimmed so it fits the 4096-token window with the question."""
    if not is_filled(p):
        return ""
    viva = (p.get("round") == "Viva")
    lines = ["[Interview setup - facts about this interview and the candidate; use them]"]
    if p.get("company"):
        lines.append(f"{'College / panel' if viva else 'Company'}: {p['company'].strip()}")
    if p.get("role"):
        lines.append(f"{'Subject / project' if viva else 'Role'}: {expand_abbrev(p['role'].strip())}")
    style = ROUND_STYLE.get(p.get("round") or "Mixed", "")
    personal = bool((p.get("resume_text") or "").strip()) or len((p.get("notes") or "").split()) >= 8
    if viva and not personal:
        style = ("This is a college viva on the topic below. The student's own project details are NOT given: "
                 "questions and answers are about the topic itself (concepts, services, how and why they are used). "
                 "Never invent a project, app, tech stack or experience for the student; use [your example] instead.")
    if style:
        lines.append(style)
    if (p.get("jd") or "").strip():
        lines.append(("Viva / course details:\n" if viva else "Job description:\n")
                     + expand_abbrev(p["jd"].strip()[:jd_chars]))
    if (p.get("resume_text") or "").strip():
        lines.append("Candidate resume:\n" + p["resume_text"].strip()[:resume_chars])
    if (p.get("notes") or "").strip():
        lines.append("Candidate notes:\n" + expand_abbrev(p["notes"].strip()[:800]))
    return "\n".join(lines)


_COMMON = {"The", "This", "That", "And", "For", "With", "From", "Our", "You", "Your", "We", "Will", "Must",
           "Good", "Strong", "Ability", "Experience", "Skills", "Responsibilities", "Requirements", "About",
           "Role", "Job", "Team", "Work", "Knowledge", "Understanding", "Education", "Projects", "Project"}


def vocab_terms(p: dict, max_terms: int = 40) -> str:
    """Names / technologies from the JD + resume so Whisper spells them right (Kubernetes, Infosys, ...)."""
    text = " ".join((p.get(k) or "") for k in ("company", "role", "jd", "resume_text", "notes"))
    counts = Counter(w for w in re.findall(r"\b(?:[A-Z][A-Za-z0-9+#.]{1,}|[A-Za-z]+[+#]{1,2}|[A-Za-z]*\.js)\b", text)
                     if w not in _COMMON)
    terms = [w for w, _ in counts.most_common(max_terms)]
    return ", ".join(terms)

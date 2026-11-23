"""
Rule-based question detector (zero LLM cost, <1ms).
Whisper often drops '?' marks, so we detect questions from wording instead.

classify(text) -> (verdict, question)
  verdict: "question" | "chatter" | "unsure"
  question: the cleaned-up question text to answer (last question-like sentence)
"""
import re

_STARTERS = (
    "what", "why", "how", "when", "where", "which", "who", "whom", "whose",
    "explain", "describe", "define", "tell me", "walk me", "talk about", "talk me",
    "can you", "could you", "would you", "will you", "do you", "does", "did you",
    "have you", "are you", "is there", "is it", "was there", "should",
    "difference between", "what's the difference", "compare", "give me", "give an",
    "write", "implement", "design", "suggest", "list", "name", "differentiate",
    "introduce yourself", "tell us", "let's talk", "lets talk", "so tell",
)

_CONTAINS = (
    "difference between", "tell me about", "explain", "walk me through",
    "what is", "what are", "what's", "how does", "how do", "how would", "how can",
    "why did", "why do", "why does", "why is", "can you explain", "could you explain",
    "what happens", "what do you", "have you worked", "your experience",
    "about yourself", "your project", "your internship", "your role",
    "strengths", "weaknesses", "trade-off", "tradeoff", "pros and cons",
    "time complexity", "space complexity", "approach", "scale", "optimize",
)

_CHATTER = {
    "thank you", "thanks", "okay", "ok", "yeah", "yes", "no", "right", "sure",
    "hello", "hi", "good morning", "good afternoon", "good evening",
    "can you hear me", "can you see my screen", "are you there", "give me a second",
    "wait a second", "one second", "just a moment", "let me share", "nice",
    "great", "cool", "alright", "got it", "i see", "uh huh", "hmm", "welcome",
    "how are you", "how's it going", "how are you doing",
}

_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _is_chatter(sentence: str) -> bool:
    low = re.sub(r"[^a-z' ]", "", sentence.lower()).strip()
    if not low:
        return True
    if low in _CHATTER:
        return True
    # the candidate handing the turn back: "Anything else you want to know, sir?", "Shall I continue?"
    if re.match(r"^(so |okay |ok )?(is there )?anything else (you|that you|u) (want|would like|wanna)|^(shall|should) i (continue|go on|proceed)|^(is|was) (that|it) (ok|okay|fine|correct)|^am i (right|audible)", low):
        return True
    if re.match(r"^(so |okay |ok )?(can|could) you (hear|see) |^(so |okay |ok )?(let me|lemme|i will|i'll) (share|screen)|^(is|are) (my|the) (audio|screen|mic|video)|^you can (hear|see)|^(am i|are we) (audible|visible|live)", low):
        return True
    # very short and made only of chatter words
    words = low.split()
    if len(words) <= 4 and all(w in {"okay", "ok", "yeah", "yes", "right", "sure", "thanks", "thank", "you", "so", "um", "uh", "alright", "good", "great", "nice", "hmm", "well"} for w in words):
        return True
    return False


_LEADING_FILLER = re.compile(r"^((okay|ok|so|now|and|alright|then|well|actually|basically|next|right|fine|good|yeah|please|kindly|sir|madam|uh|um|hmm)[ ,]+)+")

_STARTERS += (
    "what will", "what if", "how will", "how much", "how many", "how long", "suppose", "let's say",
    "lets say", "imagine", "my question", "i want to know", "i would like to know", "do you know",
    "i want you to", "i need you to", "i would like you to", "you need to", "you have to", "your task is",
    "have you heard", "have you used", "have you done", "please explain", "any idea", "what do you mean",
    "what about", "tell something", "say something", "next question", "moving on", "now tell",
    "is there any", "are there any", "is it possible", "which one", "which is", "whats", "hows",
    "who is", "what you", "any questions", "do you have", "are you familiar", "are you comfortable",
    "solve", "find", "check", "calculate", "build", "create", "print", "show me",
)
_CONTAINS += (
    "tell me something", "something about", "introduce", "your background", "why should we",
    "where do you see", "tell us", "what you know", "what you did", "what you have",
    "difference b/w", "differences between", "can you tell", "could you tell", "do you know",
    "how will you", "how would you", "what would you", "your approach", "real time example",
    "real world example", "with example", "an example", "in detail", "in brief", "brief about",
    "overview of", "how it works", "how to", "use case", "advantages", "disadvantages",
    "write a", "write the", "write code", "java code", "python code", "c++ code", "code for",
    "program for", "program to", "function to", "algorithm for", "implement", "solve",
)


_FIRST_PERSON = re.compile(r"\b(i|i'm|im|i've|ive|my|myself|me|we|our)\b")
_CANDIDATE_TALK = re.compile(
    r"\b(i'm|im|i've|ive|myself|my project|my internship|my approach|my experience|my role|my strength|my weakness|"
    r"i built|i developed|i worked|i used|i implemented|i designed|i created|i graduated|i am studying|i am from|"
    r"i don't know|i dont know|don't know how|dont know how)\b"
)


def _looks_like_question(sentence: str) -> bool:
    low = sentence.lower().strip()
    if "?" in low:
        return True
    if low.endswith(("...", "…")):          # speaker got cut off mid-sentence: wait for the rest
        return False
    low_nopunct = re.sub(r"[^a-z' ]", "", low)
    stripped = _LEADING_FILLER.sub("", low_nopunct).strip()
    for cand in (low_nopunct, stripped):
        if any(cand.startswith(s) for s in _STARTERS):
            return True
    words = stripped.split()
    # short either/or choice: "Software developer or software analyst."
    if 3 <= len(words) <= 9 and " or " in f" {stripped} " and not _FIRST_PERSON.search(stripped):
        return True
    if any(c in low_nopunct for c in _CONTAINS):
        # Reject candidate self-talk ("my approach was...", "introduce myself"),
        # but allow interviewer phrasing ("In our company we want to know...", "I want to ask about your internship")
        return not _CANDIDATE_TALK.search(low_nopunct)
    return False


def classify(text: str, min_words: int = 3) -> tuple[str, str]:
    text = _clean(text)
    if not text:
        return "chatter", ""

    sentences = [s for s in _SENT_SPLIT.split(text) if s.strip()]
    if not sentences:
        return "chatter", ""

    # Walk from the end: the most recent question-like sentence wins.
    # Include the previous sentence as context if the question is very short.
    for i in range(len(sentences) - 1, -1, -1):
        s = sentences[i].strip()
        if _is_chatter(s):
            continue
        if _looks_like_question(s) and len(s.split()) >= min_words:
            if len(s.split()) < 6 and i > 0 and not _is_chatter(sentences[i - 1]):
                return "question", _clean(f"{sentences[i - 1]} {s}")
            return "question", s

    # Nothing question-like. Pure chatter?
    if all(_is_chatter(s) for s in sentences) or len(text.split()) < min_words:
        return "chatter", ""

    # Long statement with no question cue: let the LLM decide (it can answer SKIP)
    return "unsure", text


_FOLLOWUP_PATTERNS = re.compile(
    r"^(and |but |so |okay |ok |then )?"
    r"(why|how|why so|how so|elaborate|explain more|tell me more|go deeper|more details?|"
    r"what do you mean|what does that mean|can you clarify|"
    r"give (me )?(an )?example|any example|for example|"
    r"time complexity|space complexity|complexity|big o|"
    r"trade[\s-]?offs?|any trade[\s-]?offs?|pros and cons|advantages?|disadvantages?|limitations?|"
    r"what about .+|how about .+|what if .+|suppose .+|in case .+|if (it|that|this) .+|"
    r"how (does|do|would|will|is|are) (it|that|this|they|those) .+|"
    r"why (does|do|would|did|is|are) (it|that|this|they|you) .+|"
    r"can you (elaborate|explain|expand|give an example|compare|clarify)|"
    r"is (it|that|this) .+|are (they|those) .+|"
    r"use cases?|real[\s-]world example|edge cases?|alternatives?|compare (it|that|this) .+|vs .+|versus .+)\b"
)


def is_followup(text: str) -> bool:
    """Detects live interviewer verbal follow-ups ('Why?', 'What do you mean by that?', 'What if it fails?')."""
    low = re.sub(r"[^a-z0-9'\- ]", "", (text or "").lower()).strip()
    if not low or _is_chatter(low):
        return False
    # Also catch short pronoun-referring questions ("And how does that scale?", "Why not SQL?")
    if "?" in (text or "") and len(low.split()) <= 10 and re.search(r"\b(it|that|this|those|they|not)\b", low):
        return True
    return bool(_FOLLOWUP_PATTERNS.search(low))


if __name__ == "__main__":
    tests = [
        "Okay thank you. Can you explain the difference between TCP and UDP",
        "Can you hear me properly",
        "So tell me about your internship project",
        "what is a kubernetes pod and why would you use one",
        "Okay good, let me share my screen",
        "Walk me through how you designed the database",
        "Yeah",
        "So in my last company we had a lot of microservices and the latency was high",
    ]
    for t in tests:
        print(classify(t), "<-", t)

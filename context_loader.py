"""
Local Context & RAG Loader
Scans the 'context/' folder for PDFs, DOCX, TXT, MD, and source code files.
Chunks them and retrieves the top most relevant snippets for every question
in <5 milliseconds with zero external dependencies or API calls.
"""
import os
import re
import math
from collections import Counter

CONTEXT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context")
SUPPORTED_TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".java", ".cpp", ".c", ".sql", ".html", ".css", ".json"}
STOPWORDS = {
    "the", "is", "at", "which", "on", "a", "an", "and", "or", "in", "to", "of",
    "for", "with", "as", "by", "this", "that", "it", "be", "are", "was", "were",
    "what", "why", "how", "can", "you", "your", "me", "my", "do", "does", "did",
    "explain", "describe", "tell", "about", "from", "have", "has", "had"
}

def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9_+#]+", text.lower())
    return [w for w in words if len(w) > 1 and w not in STOPWORDS]

def _read_pdf(path: str) -> str:
    try:
        import pymupdf
        doc = pymupdf.open(path)
        pages = [page.get_text("text") for page in doc]
        doc.close()
        return "\n".join(pages)
    except Exception as e:
        print(f"[Context] Failed to read PDF {path}: {e}")
        return ""

def _read_docx(path: str) -> str:
    try:
        import docx
        d = docx.Document(path)
        return "\n".join([p.text for p in d.paragraphs if p.text.strip()])
    except Exception as e:
        print(f"[Context] Failed to read DOCX {path}: {e}")
        return ""

def _chunk_text(text: str, source_name: str, chunk_size: int = 450, overlap: int = 80) -> list[dict]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk_str = text[start:end].strip()
        if chunk_str:
            tokens = _tokenize(chunk_str)
            chunks.append({
                "source": source_name,
                "text": chunk_str,
                "tokens": Counter(tokens),
                "length": max(len(tokens), 1)
            })
        start += (chunk_size - overlap)
    return chunks

class LocalContextRAG:
    def __init__(self, folder: str = CONTEXT_DIR):
        self.folder = folder
        self.chunks: list[dict] = []
        self.file_count = 0
        self.idf: dict[str, float] = {}
        self.load()

    def load(self):
        self.chunks = []
        self.file_count = 0
        if not os.path.exists(self.folder):
            os.makedirs(self.folder, exist_ok=True)
            return

        for root, _, files in os.walk(self.folder):
            for fname in files:
                if fname == "PUT_YOUR_REPORT_HERE.txt":
                    continue
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()
                raw = ""
                if ext == ".pdf":
                    raw = _read_pdf(fpath)
                elif ext == ".docx":
                    raw = _read_docx(fpath)
                elif ext in SUPPORTED_TEXT_EXTS:
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            raw = f.read()
                    except Exception:
                        raw = ""

                if raw.strip():
                    self.file_count += 1
                    self.chunks.extend(_chunk_text(raw, fname))

        # Compute IDF for BM25-like relevance scoring
        total_docs = len(self.chunks)
        if total_docs > 0:
            doc_freq = Counter()
            for c in self.chunks:
                for tok in c["tokens"].keys():
                    doc_freq[tok] += 1
            self.idf = {
                tok: math.log((total_docs + 1) / (df + 1)) + 1.0
                for tok, df in doc_freq.items()
            }

        print(f"[Context] Loaded {self.file_count} file(s) -> {len(self.chunks)} searchable chunks.")

    def vocab_hint(self, max_terms: int = 60) -> str:
        """Most frequent capitalised / technical words from context files, used to bias Whisper."""
        counts = Counter()
        for c in self.chunks:
            for w in re.findall(r"\b[A-Z][A-Za-z0-9+#.]{2,}\b", c["text"]):
                counts[w] += 1
        common = {"The", "This", "That", "And", "For", "With", "From", "Table", "Figure", "Chapter"}
        terms = [w for w, _ in counts.most_common(max_terms * 2) if w not in common][:max_terms]
        return ("Candidate's project terms: " + ", ".join(terms) + ".") if terms else ""

    def retrieve(self, query: str, top_k: int = 3) -> str:
        """Returns the top-k most relevant snippets for the given question."""
        if not self.chunks:
            return ""

        q_tokens = _tokenize(query)
        if not q_tokens:
            # Return first 2 chunks as general overview
            return "\n".join(f"[{c['source']}]: {c['text']}" for c in self.chunks[:2])

        scored = []
        for c in self.chunks:
            score = 0.0
            for qt in q_tokens:
                if qt in c["tokens"]:
                    tf = c["tokens"][qt] / c["length"]
                    score += tf * self.idf.get(qt, 1.0)
            if score > 0:
                scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_chunks = [c for _, c in scored[:top_k]]

        if not top_chunks:
            return ""

        return "\n".join(f"[{c['source']}]: {c['text']}" for c in top_chunks)

    def retrieve_with_sources(self, query: str, top_k: int = 3) -> tuple[str, list[str]]:
        """Returns (formatted_context, list_of_matched_filenames) for Hybrid RAG + HUD source badge."""
        if not self.chunks:
            return "", []

        q_tokens = _tokenize(query)
        if not q_tokens:
            return "", []

        scored = []
        for c in self.chunks:
            score = 0.0
            for qt in q_tokens:
                if qt in c["tokens"]:
                    tf = c["tokens"][qt] / c["length"]
                    score += tf * self.idf.get(qt, 1.0)
            if score > 0.08:
                scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_chunks = [c for _, c in scored[:top_k]]
        if not top_chunks:
            return "", []

        sources = []
        for c in top_chunks:
            if c["source"] not in sources:
                sources.append(c["source"])
        text = "\n".join(f"[{c['source']}]: {c['text']}" for c in top_chunks)
        return text, sources

    def list_files(self) -> list[dict]:
        """Lists all stored user documents in context/ with filename and size."""
        items = []
        if not os.path.exists(self.folder):
            return items
        for fname in sorted(os.listdir(self.folder)):
            if fname == "PUT_YOUR_REPORT_HERE.txt":
                continue
            fpath = os.path.join(self.folder, fname)
            if os.path.isfile(fpath):
                kb = max(1, round(os.path.getsize(fpath) / 1024))
                items.append({"name": fname, "size_kb": kb})
        return items

    def add_files(self, paths: list[str]) -> int:
        """Copies selected files into context/ and reloads RAG index."""
        import shutil
        os.makedirs(self.folder, exist_ok=True)
        added = 0
        for src in paths:
            if src and os.path.isfile(src):
                dst = os.path.join(self.folder, os.path.basename(src))
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.copy2(src, dst)
                added += 1
        self.load()
        return added

    def delete_file(self, filename: str) -> bool:
        """Deletes a stored file from context/ and reloads RAG index."""
        safe = os.path.basename(filename)
        fpath = os.path.join(self.folder, safe)
        if os.path.isfile(fpath):
            os.remove(fpath)
            self.load()
            return True
        return False

if __name__ == "__main__":
    rag = LocalContextRAG()
    print(f"Files: {rag.file_count}, Chunks: {len(rag.chunks)}")

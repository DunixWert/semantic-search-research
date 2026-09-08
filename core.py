from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import urllib.parse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
import numpy as np
import sklearn
from typing import Iterable

import requests
from pypdf import PdfReader
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import Normalizer


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
VALID_STATUSES = {"accepted", "rejected", "needs_review"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = urllib.parse.unquote(value).strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    match = DOI_RE.search(value)
    return match.group(0).rstrip(".,;:)]}>") if match else None


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Page:
    number: int
    text: str


def extract_pdf(path: Path) -> tuple[list[Page], dict]:
    reader = PdfReader(str(path))
    pages = []
    for number, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").replace("\x00", "").strip()
        pages.append(Page(number, text))
    metadata = reader.metadata or {}
    embedded_title = str(metadata.get("/Title") or "").strip()
    return pages, {
        "title": embedded_title or infer_title(pages, path.stem),
        "author": str(metadata.get("/Author") or "").strip(),
        "page_count": len(pages),
    }


def infer_title(pages: list[Page], fallback: str) -> str:
    """Best-effort title for PDFs whose embedded metadata is blank."""
    if not pages:
        return fallback
    candidates = []
    for raw in pages[0].text.splitlines()[:25]:
        line = re.sub(r"\s+", " ", raw).strip(" -")
        words = line.split()
        if 4 <= len(words) <= 30 and len(line) >= 25:
            lowered = line.lower()
            if not any(marker in lowered for marker in ("abstract", "arxiv:", "doi:", "http", "published as", "university", "@")):
                candidates.append(line)
    return max(candidates, key=len) if candidates else fallback


def chunk_pages(pages: Iterable[Page], target_words: int = 450, overlap_words: int = 70):
    """Create page-bounded passages so every result has an honest page number."""
    for page in pages:
        words = page.text.split()
        if not words:
            continue
        step = max(1, target_words - overlap_words)
        for index, start in enumerate(range(0, len(words), step), 1):
            text = " ".join(words[start : start + target_words]).strip()
            if text:
                yield page.number, index, text
            if start + target_words >= len(words):
                break


class Library:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.db_path = self.root / "library.sqlite3"
        self.papers_dir = self.root / "papers"
        self.text_dir = self.root / "text"
        self.logs_dir = self.root / "logs"

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.papers_dir.mkdir(exist_ok=True)
        self.text_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS papers (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    authors TEXT DEFAULT '',
                    year INTEGER,
                    venue TEXT DEFAULT '',
                    doi TEXT UNIQUE,
                    original_filename TEXT NOT NULL,
                    pdf_path TEXT NOT NULL,
                    text_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    page_count INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('accepted','rejected','needs_review')),
                    status_reason TEXT DEFAULT '',
                    verification_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS passages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    page INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    UNIQUE(paper_id, page, chunk_index)
                );
                CREATE TABLE IF NOT EXISTS assessments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    question TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_id TEXT,
                    event TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_passages_paper ON passages(paper_id);
                CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status);
                """
            )

    def _event(self, conn, paper_id: str | None, event: str, details: dict):
        conn.execute(
            "INSERT INTO events(paper_id,event,details_json,created_at) VALUES(?,?,?,?)",
            (paper_id, event, json.dumps(details, ensure_ascii=False), utc_now()),
        )

    def ingest(self, pdf: Path, question: str | None = None, doi: str | None = None) -> dict:
        self.initialize()
        pdf = pdf.resolve()
        if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF file: {pdf}")
        sha = file_hash(pdf)
        with self.connect() as conn:
            duplicate = conn.execute("SELECT * FROM papers WHERE sha256=?", (sha,)).fetchone()
            if duplicate:
                return {"duplicate": True, "paper": dict(duplicate), "reason": "identical_file_hash"}

        pages, metadata = extract_pdf(pdf)
        full_text = "\n\n".join(f"--- PAGE {p.number} ---\n{p.text}" for p in pages)
        detected_doi = normalize_doi(doi) or normalize_doi("\n".join(p.text for p in pages[:3]))
        title = metadata["title"] or pdf.stem
        paper_id = "paper_" + sha[:12]
        managed_pdf = self.papers_dir / f"{paper_id}.pdf"
        managed_text = self.text_dir / f"{paper_id}.txt"
        shutil.copy2(pdf, managed_pdf)
        managed_text.write_text(full_text, encoding="utf-8")
        now = utc_now()
        try:
            with self.connect() as conn:
                if detected_doi:
                    duplicate = conn.execute("SELECT * FROM papers WHERE doi=?", (detected_doi,)).fetchone()
                    if duplicate:
                        managed_pdf.unlink(missing_ok=True)
                        managed_text.unlink(missing_ok=True)
                        return {"duplicate": True, "paper": dict(duplicate), "reason": "same_doi"}
                conn.execute(
                    """INSERT INTO papers
                    (id,title,normalized_title,authors,doi,original_filename,pdf_path,text_path,sha256,
                     page_count,status,status_reason,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (paper_id, title, normalize_title(title), metadata["author"], detected_doi,
                     pdf.name, str(managed_pdf), str(managed_text), sha, len(pages), "needs_review",
                     "Awaiting identity verification and relevance decision", now, now),
                )
                conn.executemany(
                    "INSERT INTO passages(paper_id,page,chunk_index,text) VALUES(?,?,?,?)",
                    ((paper_id, page, index, text) for page, index, text in chunk_pages(pages)),
                )
                self._event(conn, paper_id, "ingested", {"source": str(pdf), "doi": detected_doi})
        except Exception:
            managed_pdf.unlink(missing_ok=True)
            managed_text.unlink(missing_ok=True)
            raise
        result = self.show(paper_id)
        result["extraction"] = {
            "characters": len(full_text),
            "pages_with_text": sum(bool(p.text) for p in pages),
            "needs_ocr": bool(pages) and sum(bool(p.text) for p in pages) / len(pages) < 0.5,
        }
        if question:
            result["assessment"] = self.assess(paper_id, question)
        return result

    def show(self, paper_id: str) -> dict:
        with self.connect() as conn:
            paper = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
            if not paper:
                raise KeyError(f"Unknown paper: {paper_id}")
            assessments = conn.execute(
                "SELECT question,verdict,confidence,report_json,created_at FROM assessments WHERE paper_id=? ORDER BY id DESC",
                (paper_id,),
            ).fetchall()
            result = dict(paper)
            result["verification"] = json.loads(result.pop("verification_json") or "{}")
            result["assessments"] = [dict(a) | {"report": json.loads(a["report_json"])} for a in assessments]
            for item in result["assessments"]:
                item.pop("report_json")
            return result

    def list_papers(self, status: str | None = None) -> list[dict]:
        query = "SELECT id,title,authors,year,doi,status,status_reason,page_count,verification_json FROM papers"
        params = ()
        if status:
            query += " WHERE status=?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self.connect() as conn:
            papers = [dict(row) for row in conn.execute(query, params)]
        for paper in papers:
            paper["verification"] = json.loads(paper.pop("verification_json") or "{}")
        return papers

    def update_metadata(self, paper_id: str, *, title: str, authors: str = "",
                        year: int | None = None, venue: str = "", doi: str | None = None) -> dict:
        """Save a researcher's explicit metadata correction without changing approval."""
        if not title.strip():
            raise ValueError("A paper title is required.")
        if year is not None and (isinstance(year, bool) or not isinstance(year, int) or not 1000 <= year <= 9999):
            raise ValueError("Year must be four digits, or blank if unknown.")
        normalized_doi = normalize_doi(doi)
        if doi and doi.strip() and not normalized_doi:
            raise ValueError("Enter a valid DOI, or leave it blank.")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
            if not row:
                raise KeyError(f"Unknown paper: {paper_id}")
            old = dict(row)
            fields = {"title": title.strip(), "authors": authors.strip(), "year": year,
                      "venue": venue.strip(), "doi": normalized_doi}
            identity_changed = old["title"] != fields["title"] or old["doi"] != normalized_doi
            verification = "{}" if identity_changed else old["verification_json"]
            conn.execute(
                """UPDATE papers SET title=?,normalized_title=?,authors=?,year=?,venue=?,doi=?,
                   verification_json=?,updated_at=? WHERE id=?""",
                (fields["title"], normalize_title(fields["title"]), fields["authors"], year,
                 fields["venue"], normalized_doi, verification, utc_now(), paper_id),
            )
            self._event(conn, paper_id, "metadata_updated", {
                "before": {key: old[key] for key in fields}, "after": fields,
                "identity_check_reset": identity_changed,
            })
        return self.show(paper_id)

    def text(self, paper_id: str, page: int | None = None) -> dict:
        paper = self.show(paper_id)
        if page is None:
            content = Path(paper["text_path"]).read_text(encoding="utf-8")
        else:
            # Read the original page rather than concatenating overlapping search chunks.
            source = Path(paper["text_path"])
            if source.is_file():
                full_text = source.read_text(encoding="utf-8")
                match = re.search(rf"(?m)^--- PAGE {page} ---\r?\n(.*?)(?=^--- PAGE \d+ ---\r?$|\Z)",
                                  full_text, re.S | re.M)
                if match and match.group(1).strip():
                    return {"paper_id": paper_id, "title": paper["title"], "page": page,
                            "text": match.group(1).strip()}
            with self.connect() as conn:
                parts = [row[0] for row in conn.execute(
                    "SELECT text FROM passages WHERE paper_id=? AND page=? ORDER BY chunk_index", (paper_id, page)
                )]
            if not parts:
                raise KeyError(f"No extracted text for {paper_id} page {page}")
            content = "\n\n".join(parts)
        return {"paper_id": paper_id, "title": paper["title"], "page": page, "text": content}

    def verify(self, paper_id: str, timeout: int = 15) -> dict:
        paper = self.show(paper_id)
        doi = paper.get("doi")
        if not doi:
            report = {"verified": False, "state": "missing_doi", "reason": "No DOI recorded. Identity can be checked against the original source."}
            return self._save_verification(paper_id, report)
        url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": "LocalLiteratureLibrary/0.1 (mailto:local@example.invalid)"})
            if response.status_code != 200:
                report = {"verified": False, "state": "not_found" if response.status_code == 404 else "unavailable",
                          "reason": f"Crossref returned HTTP {response.status_code}", "doi": doi}
            else:
                message = response.json()["message"]
                remote_title = " ".join(message.get("title") or [])
                remote_authors = ", ".join(
                    " ".join(filter(None, [a.get("given"), a.get("family")])) for a in message.get("author", [])
                )
                title_score = self._text_similarity(paper["title"], remote_title)
                verified = title_score >= 0.72
                date_parts = (message.get("published-print") or message.get("published-online") or message.get("issued") or {}).get("date-parts", [[]])
                report = {
                    "verified": verified,
                    "state": "matched" if verified else "mismatch",
                    "reason": "DOI resolved and title matched" if verified else "DOI resolved but title metadata needs review",
                    "doi": doi,
                    "title_similarity": round(title_score, 3),
                    "local_title": paper["title"],
                    "crossref_title": remote_title,
                    "crossref_authors": remote_authors,
                    "crossref_year": date_parts[0][0] if date_parts and date_parts[0] else None,
                    "crossref_venue": " ".join(message.get("container-title") or []),
                    "crossref_url": message.get("URL"),
                }
        except requests.RequestException as exc:
            report = {"verified": False, "state": "unavailable", "reason": f"Verification unavailable: {exc.__class__.__name__}", "doi": doi}
        return self._save_verification(paper_id, report)

    def _save_verification(self, paper_id: str, report: dict) -> dict:
        with self.connect() as conn:
            conn.execute(
                "UPDATE papers SET verification_json=?,updated_at=? WHERE id=?",
                (json.dumps(report, ensure_ascii=False), utc_now(), paper_id),
            )
            self._event(conn, paper_id, "verified", report)
        return report

    def _passage_scores(self, texts: list[str], query: str):
        """Cache repeat-query computations without changing the query-fitted ranking.

        Only numerical arrays are cached; current metadata and trust states always
        come from SQLite. Content, ordering, query and algorithm version form the key.
        JSON avoids loading executable pickle data from a cache file.
        """
        key = hashlib.sha256(json.dumps(
            ["tfidf-lsa-v1", sklearn.__version__, texts, query], ensure_ascii=False
        ).encode("utf-8")).hexdigest()
        cache_dir = self.root / "search_cache"
        cache_file = cache_dir / f"{key}.json"
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            lexical, semantic = np.asarray(cached["lexical"]), np.asarray(cached["semantic"])
            if (lexical.shape == semantic.shape == (len(texts),)
                    and np.isfinite(lexical).all() and np.isfinite(semantic).all()):
                return lexical, semantic, tuple(cached["shape"]), cached["dimensions"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        word = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True, max_features=30000)
        try:
            matrix = word.fit_transform(texts + [query])
        except ValueError as exc:
            if "empty vocabulary" not in str(exc):
                raise
            return np.zeros(len(texts)), np.zeros(len(texts)), (len(texts) + 1, 0), 0
        lexical = cosine_similarity(matrix[-1], matrix[:-1]).ravel()
        semantic = lexical.copy()
        dimensions = min(100, matrix.shape[0] - 1, matrix.shape[1] - 1)
        if dimensions >= 2:
            lsa = Normalizer(copy=False).fit_transform(TruncatedSVD(dimensions, random_state=42).fit_transform(matrix))
            semantic = cosine_similarity(lsa[-1:].reshape(1, -1), lsa[:-1]).ravel()
        try:
            cache_dir.mkdir(exist_ok=True)
            # Concurrent writers can replace the same deterministic result safely.
            import uuid
            temporary = cache_dir / f"{key}.{uuid.uuid4().hex}.tmp"
            try:
                temporary.write_text(json.dumps({"lexical": lexical.tolist(), "semantic": semantic.tolist(),
                                               "shape": matrix.shape, "dimensions": dimensions}), encoding="utf-8")
                temporary.replace(cache_file)
            finally:
                temporary.unlink(missing_ok=True)
            for stale in sorted(cache_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[64:]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass  # Caching is optional; read-only or full disks must not prevent search.
        return lexical, semantic, matrix.shape, dimensions

    @staticmethod
    def _text_similarity(left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        vectors = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).fit_transform([left, right])
        return float(cosine_similarity(vectors[0], vectors[1])[0, 0])

    def _log_search(
        self,
        query: str,
        results: list[dict],
        params: dict,
        candidate_count: int,
        latency_ms: float,
        matrix_shape: tuple[int, int] | None = None,
        dimensions: int = 0,
        zero_reason: str | None = None,
    ) -> Path | None:
        try:
            now = datetime.now(timezone.utc)
            date_str = now.strftime("%Y-%m-%d")
            day_dir = self.logs_dir / date_str
            day_dir.mkdir(parents=True, exist_ok=True)

            file_ts = now.strftime("%H%M%S_%f")
            log_file = day_dir / f"search_{file_ts}.json"
            counter = 1
            while log_file.exists():
                log_file = day_dir / f"search_{file_ts}_{counter}.json"
                counter += 1

            max_score = results[0]["score"] if results else 0.0
            min_score = results[-1]["score"] if results else 0.0

            log_entry = {
                "timestamp": now.isoformat(timespec="microseconds"),
                "date": date_str,
                "query": query,
                "parameters": params,
                "execution_stats": {
                    "latency_ms": round(latency_ms, 2),
                    "candidate_passages_evaluated": candidate_count,
                    "svd_dimensions": dimensions,
                    "matrix_shape": list(matrix_shape) if matrix_shape else None,
                    "hybrid_weights": {"lexical": 0.58, "semantic": 0.42},
                },
                "diagnostics": {
                    "zero_results": len(results) == 0,
                    "zero_results_reason": zero_reason if len(results) == 0 else None,
                    "max_score": round(max_score, 4),
                    "min_score": round(min_score, 4),
                },
                "results_count": len(results),
                "results": results,
            }

            log_file.write_text(json.dumps(log_entry, indent=2, ensure_ascii=False), encoding="utf-8")
            return log_file
        except Exception as exc:
            print(f"Warning: Failed to write search log: {exc}", file=sys.stderr)
            return None

    def list_logs(self, date_str: str | None = None, limit: int = 20) -> list[dict]:
        """Fetch summary of recent search logs. If date_str is omitted, inspects the latest date folder."""
        if not self.logs_dir.exists():
            return []
        if date_str:
            target_dirs = [self.logs_dir / date_str]
        else:
            target_dirs = sorted([d for d in self.logs_dir.iterdir() if d.is_dir()], reverse=True)

        entries = []
        for d in target_dirs:
            if not d.is_dir():
                continue
            log_files = sorted(d.glob("search_*.json"), reverse=True)
            for file_path in log_files:
                try:
                    data = json.loads(file_path.read_text(encoding="utf-8"))
                    entries.append({
                        "file": str(file_path.relative_to(self.root)),
                        "timestamp": data.get("timestamp"),
                        "date": data.get("date"),
                        "query": data.get("query"),
                        "results_count": data.get("results_count", 0),
                        "latency_ms": data.get("execution_stats", {}).get("latency_ms", 0),
                        "zero_results": data.get("diagnostics", {}).get("zero_results", False),
                        "max_score": data.get("diagnostics", {}).get("max_score", 0.0),
                        "parameters": data.get("parameters", {}),
                    })
                    if len(entries) >= limit:
                        return entries
                except Exception:
                    continue
        return entries

    def search(
        self,
        query: str,
        limit: int = 8,
        verified_only: bool = False,
        paper_id: str | None = None,
        log_search: bool = True,
        caller: str = "search",
    ) -> list[dict]:
        query = query.strip()
        if not query:
            raise ValueError("Enter a search question or phrase.")
        if limit < 1:
            raise ValueError("Results limit must be at least 1.")
        start_time = time.perf_counter()
        params_record = {
            "limit": limit,
            "verified_only": verified_only,
            "paper_id": paper_id,
            "caller": caller,
        }

        clauses = ["p.status != 'rejected'"]
        params: list[object] = []
        if verified_only:
            clauses.append("p.status = 'accepted'")
        if paper_id:
            clauses.append("p.id = ?")
            params.append(paper_id)
        sql = """SELECT x.id,x.paper_id,x.page,x.text,p.title,p.authors,p.year,p.doi,p.status,p.verification_json
                 FROM passages x JOIN papers p ON p.id=x.paper_id WHERE """ + " AND ".join(clauses)
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute(sql, params)]
        if not rows:
            latency_ms = (time.perf_counter() - start_time) * 1000
            if log_search:
                self._log_search(
                    query=query,
                    results=[],
                    params=params_record,
                    candidate_count=0,
                    latency_ms=latency_ms,
                    zero_reason="no_candidates_matching_filter",
                )
            return []

        texts = [row["text"] for row in rows]
        lexical, semantic, matrix_shape, dimensions = self._passage_scores(texts, query)
        scores = 0.58 * lexical + 0.42 * semantic
        ranked = sorted((i for i in range(len(rows)) if scores[i] > 1e-12),
                        key=lambda i: (-scores[i], rows[i]["paper_id"], rows[i]["page"]))[:limit]
        results = []
        for rank, index in enumerate(ranked, 1):
            row = rows[index]
            verification = json.loads(row.pop("verification_json") or "{}")
            results.append({
                "rank": rank,
                "score": round(float(scores[index]), 4),
                "lexical_score": round(float(lexical[index]), 4),
                "semantic_score": round(float(semantic[index]), 4),
                "match": row,
                "warning": "NEEDS REVIEW" if row["status"] == "needs_review" else None,
                "identity_verified": bool(verification.get("verified")),
            })

        latency_ms = (time.perf_counter() - start_time) * 1000
        if log_search:
            self._log_search(
                query=query,
                results=results,
                params=params_record,
                candidate_count=len(rows),
                latency_ms=latency_ms,
                matrix_shape=matrix_shape,
                dimensions=dimensions,
                zero_reason="no_positive_similarity",
            )
        return results

    def assess(self, paper_id: str, question: str, evidence_limit: int = 5) -> dict:
        evidence = self.search(question, limit=evidence_limit, paper_id=paper_id, caller="assess")
        best = evidence[0]["score"] if evidence else 0.0
        confidence = min(0.95, max(0.05, best * 2.2))
        verdict = "accept" if best >= 0.16 else "review" if best >= 0.07 else "reject"
        report = {
            "question": question,
            "recommended_verdict": verdict,
            "confidence": round(confidence, 3),
            "explanation": "Automated retrieval-based screening; inspect the cited passages before deciding. Confidence is an uncalibrated similarity heuristic, not a probability of suitability or correctness.",
            "confidence_kind": "uncalibrated_retrieval_heuristic",
            "evidence": evidence,
        }
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO assessments(paper_id,question,verdict,confidence,report_json,created_at) VALUES(?,?,?,?,?,?)",
                (paper_id, question, verdict, confidence, json.dumps(report, ensure_ascii=False), utc_now()),
            )
            self._event(conn, paper_id, "assessed", {"question": question, "verdict": verdict})
        return report

    def decide(self, paper_id: str, status: str, reason: str) -> dict:
        if status not in VALID_STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(sorted(VALID_STATUSES))}")
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
                raise KeyError(f"Unknown paper: {paper_id}")
            conn.execute("UPDATE papers SET status=?,status_reason=?,updated_at=? WHERE id=?", (status, reason, utc_now(), paper_id))
            self._event(conn, paper_id, "decision", {"status": status, "reason": reason})
        return self.show(paper_id)

    def purge(self, paper_id: str, confirmation: str) -> dict:
        if confirmation != paper_id:
            raise ValueError(f"Confirmation must exactly equal {paper_id}")
        paper = self.show(paper_id)
        with self.connect() as conn:
            conn.execute("DELETE FROM papers WHERE id=?", (paper_id,))
            self._event(conn, paper_id, "purged", {"title": paper["title"]})
        Path(paper["pdf_path"]).unlink(missing_ok=True)
        Path(paper["text_path"]).unlink(missing_ok=True)
        return {"purged": paper_id, "title": paper["title"], "recoverable": False}

    def benchmark(self, benchmark_file: Path, limit: int = 5) -> dict:
        cases = json.loads(benchmark_file.read_text(encoding="utf-8"))
        details = []
        hits = 0
        reciprocal_ranks = []
        for case in cases:
            results = self.search(case["query"], limit=limit, verified_only=case.get("verified_only", False))
            expected_papers = set(case.get("expected_paper_ids", []))
            expected_terms = [term.lower() for term in case.get("expected_terms", [])]
            found_rank = None
            for result in results:
                match = result["match"]
                paper_ok = not expected_papers or match["paper_id"] in expected_papers
                terms_ok = not expected_terms or any(term in match["text"].lower() for term in expected_terms)
                if paper_ok and terms_ok:
                    found_rank = result["rank"]
                    break
            hit = found_rank is not None
            hits += int(hit)
            reciprocal_ranks.append(1 / found_rank if found_rank else 0)
            details.append({"name": case.get("name", case["query"]), "hit": hit, "rank": found_rank})
        total = len(cases)
        return {
            "cases": total,
            f"recall_at_{limit}": round(hits / total, 3) if total else 0,
            "mean_reciprocal_rank": round(sum(reciprocal_ranks) / total, 3) if total else 0,
            "details": details,
        }

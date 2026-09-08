"""Compare cached retrieval with the prior uncached algorithm on the real corpus.

This is a small regression/performance check, not an exhaustive relevance benchmark.
It writes cache/log artifacts but never changes paper records or decisions.
"""
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import Normalizer

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from literature_library.core import Library

project = Path(__file__).resolve().parents[3]
library = Library(project / "LiteratureLibrary")
before = hashlib.sha256(library.db_path.read_bytes()).hexdigest()
benchmark_path = Path(__file__).resolve().parents[1] / "benchmark.library.json"
cases = json.loads(benchmark_path.read_text(encoding="utf-8"))
with library.connect() as conn:
    rows = [dict(row) for row in conn.execute("""SELECT x.id,x.paper_id,x.page,x.text
        FROM passages x JOIN papers p ON p.id=x.paper_id WHERE p.status != 'rejected'""")]
texts = [row["text"] for row in rows]
comparisons = []
for case in cases:
    started = time.perf_counter()
    matrix = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True,
                             max_features=30000).fit_transform(texts + [case["query"]])
    lexical = cosine_similarity(matrix[-1], matrix[:-1]).ravel()
    semantic = lexical.copy()
    dimensions = min(100, matrix.shape[0] - 1, matrix.shape[1] - 1)
    if dimensions >= 2:
        lsa = Normalizer(copy=False).fit_transform(TruncatedSVD(dimensions, random_state=42).fit_transform(matrix))
        semantic = cosine_similarity(lsa[-1:].reshape(1, -1), lsa[:-1]).ravel()
    scores = 0.58 * lexical + 0.42 * semantic
    ranked = sorted(range(len(rows)), key=lambda i: (-scores[i], rows[i]["paper_id"], rows[i]["page"]))[:5]
    baseline_ms = (time.perf_counter() - started) * 1000
    expected = [(rows[i]["id"], round(float(scores[i]), 4)) for i in ranked]
    # Use an empty temporary cache for a genuine cold run, while reading the same corpus.
    with tempfile.TemporaryDirectory() as temp:
        cold_library = Library(Path(temp))
        cold_library.db_path = library.db_path
        started = time.perf_counter()
        cold = cold_library.search(case["query"], limit=5, log_search=False)
        cold_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        warm = cold_library.search(case["query"], limit=5, log_search=False)
        warm_ms = (time.perf_counter() - started) * 1000
    actual = [(r["match"]["id"], r["score"]) for r in cold]
    assert actual == expected, case["name"]
    assert cold == warm, case["name"]
    comparisons.append({"name": case["name"], "top_5_identical_to_prior_algorithm": True,
                        "baseline_ms": round(baseline_ms, 2), "cold_ms": round(cold_ms, 2),
                        "repeat_ms": round(warm_ms, 2)})
report = {"passages": len(rows), "comparisons": comparisons,
          "benchmark": library.benchmark(benchmark_path),
          "database_unchanged": before == hashlib.sha256(library.db_path.read_bytes()).hexdigest()}
assert report["database_unchanged"]
output = project / "docs" / "literature-ui"
output.mkdir(parents=True, exist_ok=True)
(output / "retrieval-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))

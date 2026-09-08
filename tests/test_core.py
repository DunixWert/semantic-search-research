import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from literature_library.core import Library, Page, chunk_pages, infer_title, normalize_doi


class CoreTests(unittest.TestCase):
    def test_normalize_doi(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1234/ABC.9."), "10.1234/abc.9")

    def test_chunks_never_cross_pages(self):
        chunks = list(chunk_pages([Page(1, "one " * 600), Page(2, "two " * 20)]))
        self.assertEqual({page for page, _, text in chunks if "one" in text}, {1})
        self.assertEqual({page for page, _, text in chunks if "two" in text}, {2})

    def test_infer_title_from_first_page(self):
        pages = [Page(1, "Journal header\nA Reliable Method for Short Term Solar Forecasting\nAbstract\nBody")]
        self.assertEqual(infer_title(pages, "file-name"), "A Reliable Method for Short Term Solar Forecasting")

    def test_search_warns_for_needs_review_and_excludes_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            lib = Library(Path(directory)); lib.initialize()
            now = "2026-01-01T00:00:00+00:00"
            with lib.connect() as conn:
                conn.execute("""INSERT INTO papers
                    (id,title,normalized_title,original_filename,pdf_path,text_path,sha256,page_count,status,status_reason,created_at,updated_at)
                    VALUES('p1','Spatial Solar','spatial solar','a.pdf','a.pdf','a.txt','hash1',1,'needs_review','check',?,?)""", (now, now))
                conn.execute("INSERT INTO passages(paper_id,page,chunk_index,text) VALUES('p1',3,1,'Cloud fronts propagate between nearby photovoltaic sites')")
            found = lib.search("cloud movement across solar sites")
            self.assertEqual(found[0]["match"]["page"], 3)
            self.assertEqual(found[0]["warning"], "NEEDS REVIEW")
            lib.decide("p1", "rejected", "not relevant")
            self.assertEqual(lib.search("cloud movement"), [])

    def test_benchmark_reports_recall_and_rank(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lib = Library(root); lib.initialize()
            now = "2026-01-01T00:00:00+00:00"
            with lib.connect() as conn:
                conn.execute("""INSERT INTO papers
                    (id,title,normalized_title,original_filename,pdf_path,text_path,sha256,page_count,status,status_reason,created_at,updated_at)
                    VALUES('p1','Forecast Evaluation','forecast evaluation','a.pdf','a.pdf','a.txt','hash2',1,'accepted','checked',?,?)""", (now, now))
                conn.execute("INSERT INTO passages(paper_id,page,chunk_index,text) VALUES('p1',4,1,'Rolling origin evaluation prevents future observations leaking into model training')")
            benchmark = root / "benchmark.json"
            # Include a shared concept: an all-zero vocabulary match must not count as a hit.
            benchmark.write_text(json.dumps([{"query": "avoid temporal leakage evaluation", "expected_paper_ids": ["p1"], "expected_terms": ["rolling origin"]}]), encoding="utf-8")
            report = lib.benchmark(benchmark)
            self.assertEqual(report["recall_at_5"], 1.0)
            self.assertEqual(report["mean_reciprocal_rank"], 1.0)

    def test_search_creates_daily_json_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lib = Library(root)
            lib.initialize()
            now = "2026-01-01T00:00:00+00:00"
            with lib.connect() as conn:
                conn.execute("""INSERT INTO papers
                    (id,title,normalized_title,original_filename,pdf_path,text_path,sha256,page_count,status,status_reason,created_at,updated_at)
                    VALUES('p1','Solar Angles','solar angles','a.pdf','a.pdf','a.txt','hash3',1,'accepted','checked',?,?)""", (now, now))
                conn.execute("INSERT INTO passages(paper_id,page,chunk_index,text) VALUES('p1',1,1,'Solar zenith angle dictates daylight solar irradiance')")
            found = lib.search("solar zenith angle")
            self.assertEqual(len(found), 1)

            # Check daily log folder and log content
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            day_dir = lib.logs_dir / today_str
            self.assertTrue(day_dir.is_dir(), f"Daily log directory {day_dir} was not created")
            log_files = list(day_dir.glob("search_*.json"))
            self.assertEqual(len(log_files), 1)

            log_data = json.loads(log_files[0].read_text(encoding="utf-8"))
            self.assertEqual(log_data["query"], "solar zenith angle")
            self.assertEqual(log_data["results_count"], 1)
            self.assertFalse(log_data["diagnostics"]["zero_results"])
            self.assertEqual(log_data["results"][0]["match"]["paper_id"], "p1")
            self.assertIn("lexical_score", log_data["results"][0])
            self.assertIn("semantic_score", log_data["results"][0])
            self.assertGreater(log_data["execution_stats"]["candidate_passages_evaluated"], 0)

    def test_search_logs_zero_results_and_list_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lib = Library(root)
            lib.initialize()
            # Search against empty library
            found = lib.search("nonexistent topic")
            self.assertEqual(found, [])

            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_files = list((lib.logs_dir / today_str).glob("search_*.json"))
            self.assertEqual(len(log_files), 1)

            log_data = json.loads(log_files[0].read_text(encoding="utf-8"))
            self.assertTrue(log_data["diagnostics"]["zero_results"])
            self.assertEqual(log_data["diagnostics"]["zero_results_reason"], "no_candidates_matching_filter")

            # Test list_logs helper
            summaries = lib.list_logs()
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["query"], "nonexistent topic")
            self.assertTrue(summaries[0]["zero_results"])


if __name__ == "__main__":
    unittest.main()

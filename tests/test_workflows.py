import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from literature_library.core import Library
from literature_library.presentation import citation_text, group_results, identity_label


def fixture(root):
    lib = Library(Path(root))
    lib.initialize()
    for i, status in enumerate(("accepted", "needs_review", "rejected"), 1):
        text_path = lib.text_dir / f"p{i}.txt"
        text_path.write_text("--- PAGE 1 ---\nSolar cloud movement across nearby sites.\n\n"
                             "--- PAGE 2 ---\nForecast evaluation uses rolling origin.\n", encoding="utf-8")
        with lib.connect() as conn:
            conn.execute("""INSERT INTO papers
                (id,title,normalized_title,authors,year,doi,original_filename,pdf_path,text_path,sha256,
                 page_count,status,status_reason,verification_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"p{i}", f"Solar cloud forecasting {i}", f"solar cloud forecasting {i}", "A. Researcher", 2025,
                 f"10.1234/p{i}", f"p{i}.pdf", str(lib.papers_dir / f"p{i}.pdf"), str(text_path), f"hash{i}",
                 2, status, "Researcher's original decision", "{}", "2026-01-01", "2026-01-01"))
            for page, text in ((1, "Solar cloud movement across nearby sites."), (2, "Forecast evaluation uses rolling origin.")):
                conn.execute("INSERT INTO passages(paper_id,page,chunk_index,text) VALUES(?,?,1,?)", (f"p{i}", page, text))
    return lib


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.lib = fixture(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_verification_preserves_each_human_decision_and_reason(self):
        for status, paper_id in (("accepted", "p1"), ("needs_review", "p2"), ("rejected", "p3")):
            for report in ({"verified": True, "reason": "Matched"}, {"verified": False, "reason": "Unavailable"}):
                self.lib._save_verification(paper_id, report)
                paper = self.lib.show(paper_id)
                self.assertEqual(paper["status"], status)
                self.assertEqual(paper["status_reason"], "Researcher's original decision")
                self.assertEqual(paper["verification"], report)

    def test_identity_labels_are_independent_of_approval(self):
        self.assertEqual(identity_label(self.lib.show("p1")), "Identity not checked")
        self.lib._save_verification("p2", {"verified": True, "reason": "Matched"})
        papers = {p["id"]: p for p in self.lib.list_papers()}
        self.assertEqual(identity_label(papers["p2"]), "DOI matched")
        self.assertEqual(papers["p2"]["status"], "needs_review")
        self.assertEqual(identity_label({"status": "accepted"}), "No DOI recorded")

    def test_identity_failure_states_are_distinct(self):
        import requests
        with patch("literature_library.core.requests.get", side_effect=requests.Timeout):
            self.assertEqual(self.lib.verify("p1")["state"], "unavailable")
        with patch("literature_library.core.requests.get") as request:
            request.return_value.status_code = 404
            self.assertEqual(self.lib.verify("p1")["state"], "not_found")
            request.return_value.status_code = 200
            request.return_value.json.return_value = {"message": {"title": ["Completely unrelated publication"]}}
            self.assertEqual(self.lib.verify("p1")["state"], "mismatch")
        self.assertEqual(self.lib.show("p1")["status"], "accepted")

    def test_metadata_edit_is_audited_and_invalidates_only_identity(self):
        self.lib._save_verification("p1", {"verified": True, "reason": "Matched"})
        paper = self.lib.update_metadata("p1", title="Corrected title", authors="Correct Author", year=2024, doi="10.1234/p1")
        self.assertEqual((paper["title"], paper["year"], paper["verification"], paper["status"]), ("Corrected title", 2024, {}, "accepted"))
        with self.lib.connect() as conn:
            event = json.loads(conn.execute("SELECT details_json FROM events WHERE event='metadata_updated'").fetchone()[0])
        self.assertEqual(event["before"]["title"], "Solar cloud forecasting 1")
        self.assertEqual(event["after"]["title"], "Corrected title")

    def test_invalid_metadata_and_duplicate_doi_do_not_partially_save(self):
        before = self.lib.show("p1")
        for fields in ({"title": ""}, {"title": "test", "year": 12}, {"title": "test", "doi": "invalid"},
                       {"title": "test", "doi": "10.1234/p2"}):
            with self.assertRaises(Exception):
                self.lib.update_metadata("p1", **fields)
            self.assertEqual(self.lib.show("p1"), before)

    def test_page_text_uses_original_without_duplicate_overlap(self):
        with self.lib.connect() as conn:
            conn.execute("INSERT INTO passages(paper_id,page,chunk_index,text) VALUES('p1',1,2,'duplicate overlap')")
        self.assertEqual(self.lib.text("p1", page=1)["text"], "Solar cloud movement across nearby sites.")
        self.assertNotIn("Solar", self.lib.text("p1", page=2)["text"])

    def test_cache_reused_across_instances_but_never_caches_trust(self):
        first = self.lib.search("solar cloud")
        second_lib = Library(self.lib.root)
        with patch("literature_library.core.TfidfVectorizer", side_effect=AssertionError("Unexpected refit")):
            second = second_lib.search("solar cloud")
            self.assertEqual(first, second)
            second_lib._save_verification("p2", {"verified": True, "reason": "matched"})
            updated = second_lib.search("solar cloud")
        self.assertTrue(next(r for r in updated if r["match"]["paper_id"] == "p2")["identity_verified"])

    def test_cache_invalidates_on_text_edit_and_rejection_excludes_paper(self):
        self.lib.search("solar cloud")
        before = len(list((self.lib.root / "search_cache").glob("*.json")))
        with self.lib.connect() as conn:
            conn.execute("UPDATE passages SET text='solar new data' WHERE paper_id='p2'")
        self.lib.search("solar cloud")
        self.assertGreater(len(list((self.lib.root / "search_cache").glob("*.json"))), before)
        self.lib.decide("p2", "rejected", "test decision")
        self.assertNotIn("p2", {r["match"]["paper_id"] for r in self.lib.search("solar cloud")})

    def test_corrupt_cache_falls_back_to_search(self):
        first = self.lib.search("solar cloud")
        for path in (self.lib.root / "search_cache").glob("*.json"):
            path.write_text("broken", encoding="utf-8")
        self.assertEqual(first, self.lib.search("solar cloud"))

    def test_empty_and_zero_similarity_searches(self):
        with self.assertRaises(ValueError):
            self.lib.search(" ")
        with self.assertRaises(ValueError):
            self.lib.search("solar", limit=0)
        self.assertEqual(self.lib.search("zyxwvutsrqponmlk"), [])

    def test_grouping_and_unreviewed_export(self):
        results = self.lib.search("solar cloud")
        groups = group_results(results + results)
        self.assertEqual(sum(map(len, groups)), len({(r['match']['paper_id'], r['match']['page']) for r in results}))
        self.assertTrue(citation_text(self.lib.show("p2"), 2).startswith("[UNREVIEWED"))
        self.assertEqual(citation_text(self.lib.show("p1"), 2), "(A. Researcher, 2025, p1, p. 2)")


class GUIWorkflowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from literature_library.gui import LiteratureLibraryGUI
        self.directory = tempfile.TemporaryDirectory()
        self.lib = fixture(self.directory.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = LiteratureLibraryGUI(self.root, self.lib)
        self.root.update_idletasks()

    def tearDown(self):
        self.app.close()
        self.directory.cleanup()

    def test_search_grouping_reader_and_options(self):
        self.app.display_results(self.lib.search("solar cloud"), "solar cloud")
        self.root.update_idletasks()
        self.assertEqual(len(self.app.search_tree.get_children()), 2)
        self.assertIn("Solar cloud", self.app.search_reader.text.get("1.0", "end"))
        self.assertEqual(self.app.search_reader.mode, "page")
        self.assertFalse(self.app.search_reader.menu.winfo_ismapped())
        self.assertFalse(self.app.search_options.winfo_ismapped())
        self.app.search_reader.turn(1)
        self.assertIn("rolling origin", self.app.search_reader.text.get("1.0", "end"))

    def test_empty_approved_search_offers_broader_search(self):
        self.app.display_results([], "solar", True)
        self.assertEqual(self.app.show_all_button.winfo_manager(), "pack")
        self.assertIn("approved papers", self.app.search_summary.cget("text"))
        self.app.display_results([], "solar", False)
        self.assertEqual(self.app.show_all_button.winfo_manager(), "")

    def test_review_approve_undo_and_later_on_fixture_only(self):
        self.assertEqual(self.app.review_reader.paper["id"], "p2")
        self.app.defer()
        self.assertIsNone(self.app.review_reader.paper)
        self.assertEqual(self.lib.show("p2")["status"], "needs_review")
        self.app.restore_deferred()
        self.app.decide("accepted")
        self.assertEqual(self.lib.show("p2")["status"], "accepted")
        self.assertIsNone(self.app.review_reader.paper)
        self.app.undo()
        self.assertEqual(self.lib.show("p2")["status"], "needs_review")
        self.assertEqual(self.app.review_reader.paper["id"], "p2")

    def test_rejection_refreshes_search_and_disables_empty_actions(self):
        self.lib.decide("p1", "rejected", "fixture")
        self.app.display_results(self.lib.search("solar cloud"), "solar cloud")
        self.app.decide("rejected")
        self.assertEqual(self.app.current_search_results, [])
        self.assertIsNone(self.app.search_reader.paper)
        self.assertTrue(self.app.approve.instate(["disabled"]))

    def test_worker_failure_reaches_ui_and_restores_search_button(self):
        def fail():
            raise ValueError("fixture failure")
        self.app.run_job("Working", fail, lambda _: self.fail("Should not finish"))
        deadline = time.monotonic() + 5
        with patch("literature_library.gui.messagebox.showerror") as error:
            while self.app.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
        self.assertFalse(self.app.busy)
        self.assertTrue(self.app.btn_search.instate(["!disabled"]))
        self.assertIn("fixture failure", self.app.status.get())
        error.assert_called_once()

    def test_resizable_panels_and_compact_review_actions_stay_visible(self):
        self.root.geometry("1060x720")
        self.root.deiconify()
        self.root.update()
        self.app.search_panes.sash_place(0, 420, 0)
        self.app.save_layout()
        saved = json.loads(self.app.layout_path.read_text(encoding="utf-8"))
        self.assertAlmostEqual(saved["search"], 420 / self.app.search_panes.winfo_width(), places=2)
        self.app.notebook.select(self.app.tab_review)
        self.root.update()
        self.assertTrue(self.app.approve.winfo_ismapped())
        self.assertLess(self.app.approve.winfo_rooty() + self.app.approve.winfo_height(), self.root.winfo_rooty() + 720)
        self.assertGreater(self.app.review_reader.text.winfo_height(), 250)


if __name__ == "__main__":
    unittest.main()

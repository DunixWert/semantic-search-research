import os
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from literature_library.core import Library
from literature_library.gui import LiteratureLibraryGUI
from literature_library.pdf_viewer import render_page, PDFViewer


def create_test_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    text = (
        "Solar photovoltaic forecasting research.\n\n"
        "Grid frequency stability depends on rotational inertia and fast frequency response.\n"
        "Machine learning models predict solar export power under extreme cloud variability.\n"
    )
    page.insert_text((50, 100), text, fontsize=12)
    doc.save(str(path))
    doc.close()


class PDFViewerTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pdf_path = Path(self.temp_dir.name) / "sample.pdf"
        create_test_pdf(self.pdf_path)

        # Set up a minimal library fixture
        self.lib = Library(Path(self.temp_dir.name) / "lib")
        self.lib.initialize()
        text_path = self.lib.text_dir / "sample.txt"
        text_path.write_text("--- PAGE 1 ---\nSolar photovoltaic forecasting research.\n", encoding="utf-8")
        with self.lib.connect() as conn:
            conn.execute("""INSERT INTO papers
                (id,title,normalized_title,authors,year,doi,original_filename,pdf_path,text_path,sha256,
                 page_count,status,status_reason,verification_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("p_test", "Solar Forecasting Review", "solar forecasting review", "A. Author", 2025,
                 "10.1234/test", "sample.pdf", str(self.pdf_path), str(text_path), "testsha",
                 1, "needs_review", "Testing", "{}", "2026-01-01", "2026-01-01"))
            conn.execute("INSERT INTO passages(paper_id,page,chunk_index,text) VALUES(?,?,1,?)",
                         ("p_test", 1, "Solar photovoltaic forecasting research."))

        self.root = tk.Tk()
        self.root.withdraw()
        self.app = LiteratureLibraryGUI(self.root, self.lib)
        self.root.update_idletasks()

    def tearDown(self):
        self.app.close()
        self.temp_dir.cleanup()

    def test_render_page_highlights_and_targets_match(self):
        img, rects, target = render_page(
            str(self.pdf_path),
            page_number=1,
            width=600,
            query="frequency stability",
            passage="Grid frequency stability depends on rotational inertia"
        )
        self.assertIsNotNone(img)
        self.assertGreaterEqual(img.width, 300)
        self.assertGreaterEqual(len(rects), 1)
        self.assertIsNotNone(target)
        self.assertEqual(len(target), 4)

    def test_view_toggle_switches_between_pdf_and_text(self):
        reader = self.app.search_reader
        reader.load("p_test")
        self.assertEqual(reader.view, "pdf")
        self.assertEqual(reader.view_toggle.cget("text"), "📄 PDF")

        # Toggle to text view
        reader.toggle_view()
        self.assertEqual(reader.view, "text")
        self.assertEqual(reader.view_toggle.cget("text"), "📝 Text")
        self.assertEqual(reader.pdf.winfo_manager(), "")
        self.assertEqual(reader.text.master.winfo_manager(), "pack")

        # Toggle back to PDF view
        reader.toggle_view()
        self.assertEqual(reader.view, "pdf")
        self.assertEqual(reader.view_toggle.cget("text"), "📄 PDF")
        self.assertEqual(reader.pdf.winfo_manager(), "pack")
        self.assertEqual(reader.text.master.winfo_manager(), "")

    def test_search_to_review_carries_result_page_and_view(self):
        sample_result = {
            "query": "rotational inertia",
            "score": 0.85,
            "lexical_score": 0.8,
            "semantic_score": 0.9,
            "match": {
                "paper_id": "p_test",
                "page": 1,
                "text": "Grid frequency stability depends on rotational inertia"
            }
        }
        # In search reader, load the result
        self.app.search_reader.load("p_test", sample_result, page=1)
        self.assertEqual(self.app.search_reader.query, "rotational inertia")
        self.assertEqual(self.app.search_reader.page, 1)

        # Open in Review with result and page carried over
        self.app.open_review("p_test", result=sample_result, page=1)
        self.assertEqual(self.app.notebook.select(), str(self.app.tab_review))
        self.assertEqual(self.app.review_reader.paper["id"], "p_test")
        self.assertEqual(self.app.review_reader.page, 1)
        self.assertEqual(self.app.review_reader.query, "rotational inertia")
        self.assertEqual(self.app.review_reader.result, sample_result)
        self.assertEqual(self.app.review_reader.view, self.app.search_reader.view)


if __name__ == "__main__":
    unittest.main()

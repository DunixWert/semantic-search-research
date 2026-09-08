"""Manual visual QA of this app's own windows; never changes paper decisions.

Run from code/: Starting_Model/.venv/Scripts/python.exe literature_library/tests/capture_ui.py
"""
import ctypes
import json
import sys
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from literature_library.core import Library
from literature_library.gui import LiteratureLibraryGUI

project = Path(__file__).resolve().parents[3]
output = project / "docs" / "literature-ui"
output.mkdir(parents=True, exist_ok=True)
library = Library(project / "LiteratureLibrary")
root = tk.Tk()
app = LiteratureLibraryGUI(root, library)
app.layout_path = output / "preview-layout.json"
app.layout = {}
root.geometry("1280x860+20+20")
errors = []
root.report_callback_exception = lambda *args: errors.append(str(args[1]))


def snapshot(name):
    root.update_idletasks()
    root.update()
    root.after(250, root.quit)
    root.mainloop()
    ctypes.windll.user32.GetParent.restype = ctypes.c_void_p
    window = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
    ImageGrab.grab(window=window).save(output / f"{name}.png")
    return {"window": [root.winfo_width(), root.winfo_height()],
            "search_reader": [app.search_reader.text.winfo_width(), app.search_reader.text.winfo_height()],
            "review_reader": [app.review_reader.text.winfo_width(), app.review_reader.text.winfo_height()]}


try:
    query = "L2 weight decay regularization neural network"
    app.search_entry.set(query)
    app.display_results(library.search(query, limit=16, log_search=False), query)
    metrics = {"search": snapshot("search")}
    app.notebook.select(app.tab_review)
    metrics["review"] = snapshot("review")
    app.notebook.select(app.tab_library)
    metrics["library"] = snapshot("library")
    app.notebook.select(app.tab_search)
    root.geometry("1060x720+20+20")
    metrics["compact"] = snapshot("search-compact")
    app.notebook.select(app.tab_review)
    metrics["review_compact"] = snapshot("review-compact")
    metrics["callback_errors"] = errors
    (output / "layout-check.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics))
finally:
    app.close()

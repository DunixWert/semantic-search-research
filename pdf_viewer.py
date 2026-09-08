"""Embedded PDF preview. Source PDFs are opened read-only and never saved.

Rendering is optional: the GUI keeps extracted-text fallback when PyMuPDF is absent.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk


def render_page(path, page_number, width, query="", passage="", zoom=1.0):
    import pymupdf
    from PIL import Image, ImageDraw
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise ValueError("This PDF requires a password. Use Open PDF instead.")
        if not 1 <= page_number <= len(document):
            raise ValueError("The requested page is not present in the PDF.")
        page = document[page_number - 1]
        scale = min(4.0, max(0.35, width / page.rect.width * zoom))
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        terms = set(re.findall(r"\b\w{2,}\b", query.lower())) - ENGLISH_STOP_WORDS
        hits = []
        for term in sorted(terms):
            for rectangle in page.search_for(term):
                hits.append((term, rectangle * page.rotation_matrix))
        anchor = None
        words = passage.split()
        # Short literal sequences tolerate the extractor's passage boundaries.
        for offset in range(0, min(48, len(words) - 5), 6):
            found = page.search_for(" ".join(words[offset:offset + 6]))
            if found:
                anchor = found[0] * page.rotation_matrix
                break
        target = None
        if hits:
            # Prefer a cluster containing several different query terms; break ties
            # using proximity to the retrieved passage rather than page headers.
            def relevance(hit):
                rectangle = hit[1]
                nearby = {term for term, other in hits if abs(other.y0 - rectangle.y0) < 70 and abs(other.x0 - rectangle.x0) < 260}
                distance = abs(rectangle.y0 - anchor.y0) if anchor else rectangle.y0
                return (-len(nearby), distance, rectangle.y0)
            target = min(hits, key=relevance)[1]
        elif anchor:
            hits = [("passage", anchor)]
            target = anchor
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        rectangles = []
        for _, rectangle in hits:
            box = tuple(coordinate * scale for coordinate in rectangle)
            rectangles.append(box)
            draw.rectangle(box, fill=(255, 210, 35, 85))
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        return image, rectangles, tuple(value * scale for value in target) if target else None


class PDFViewer(ttk.Frame):
    """One rendered page, scrollable and zoomable, with automatic match targeting."""
    def __init__(self, parent, on_error):
        super().__init__(parent)
        self.on_error = on_error
        self.request = None
        self.photo = None
        self.zoom = 1.0
        self.pending = None
        self.last_width = 0
        self.rectangles = []
        self.canvas = tk.Canvas(self, background="#dce3e8", highlightthickness=0)
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<Configure>", self.resize)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.bind("<Destroy>", self.cleanup)

    def show(self, path, page, query="", passage=""):
        self.request = (path, page, query, passage)
        self.draw(jump=True)

    def draw(self, jump=False):
        self.pending = None
        if not self.request:
            return
        try:
            from PIL import ImageTk
            path, page, query, passage = self.request
            width = max(300, self.canvas.winfo_width() - 12)
            self.last_width = self.canvas.winfo_width()
            old_y = self.canvas.yview()[0]
            image, self.rectangles, target = render_page(path, page, width, query, passage, self.zoom)
            self.photo = ImageTk.PhotoImage(image, master=self.canvas)
            self.canvas.delete("all")
            self.canvas.create_image(6, 6, image=self.photo, anchor="nw")
            self.canvas.configure(scrollregion=(0, 0, image.width + 12, image.height + 12))
            if jump:
                self.canvas.yview_moveto(max(0, (target[1] - 50) / (image.height + 12)) if target else 0)
                self.canvas.xview_moveto(max(0, (target[0] - 30) / (image.width + 12)) if target else 0)
            else:
                self.canvas.yview_moveto(old_y)
        except Exception as exc:
            self.request = None
            self.on_error(str(exc))

    def resize(self, event):
        if self.request and abs(event.width - self.last_width) > 8:
            if self.pending:
                self.after_cancel(self.pending)
            self.pending = self.after(180, self.draw)

    def set_zoom(self, factor):
        self.zoom = min(3.0, max(0.5, factor))
        self.draw()

    def wheel(self, event):
        if event.state & 0x4:
            self.set_zoom(self.zoom * (1.1 if event.delta > 0 else 1 / 1.1))
        else:
            self.canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
        return "break"

    def cleanup(self, event):
        if event.widget is self and self.pending:
            self.after_cancel(self.pending)
            self.pending = None

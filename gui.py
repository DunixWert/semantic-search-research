"""Reading-first native interface; technical tools are progressively disclosed."""
from __future__ import annotations

import json
import os
import queue
import re
import textwrap
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .core import Library
from .presentation import APPROVAL_LABELS, citation_text, group_results, identity_label
from .pdf_viewer import PDFViewer


class Disclosure(ttk.Frame):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.title, self.open = title, False
        self.toggle = ttk.Button(self, text=f"▸ {title}", command=self.flip, style="Quiet.TButton")
        self.toggle.pack(anchor="w")
        self.body = ttk.Frame(self, padding=(8, 6))

    def flip(self):
        self.open = not self.open
        self.toggle.configure(text=f"{'▾' if self.open else '▸'} {self.title}")
        if self.open:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()


class Tooltip:
    """Give compact icon buttons a readable hover label."""
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.timer = self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def schedule(self, _=None):
        self.hide()
        self.timer = self.widget.after(450, self.show)

    def show(self):
        self.timer = None
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.geometry(f"+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 5}")
        ttk.Label(self.window, text=self.text, padding=(8, 5), relief="solid", borderwidth=1).pack()

    def hide(self, _=None):
        if self.timer:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window:
            self.window.destroy()
            self.window = None


def put_text(widget, content):
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", content)
    widget.configure(state="disabled")


def textbox(parent, font=("Segoe UI", 10)):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    text = tk.Text(frame, wrap="word", font=font, height=6, padx=16, pady=14,
                   background="white", foreground="#263244", relief="flat", spacing1=2, spacing3=6,
                   selectbackground="#cce6ed", state="disabled")
    scroll = ttk.Scrollbar(frame, command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    return text


class Reader(ttk.Frame):
    def __init__(self, parent, app, review=False):
        super().__init__(parent, padding=(16, 4, 0, 0))
        self.app, self.review = app, review
        self.paper = self.result = None
        self.page, self.mode = 1, "page"
        self.view = "pdf"
        self.query = ""
        self.buttons = []
        self.title = ttk.Label(self, style="ReaderTitle.TLabel", wraplength=500)
        self.title.pack(fill="x", pady=(0, 3))
        self.byline = ttk.Label(self, style="Muted.TLabel", wraplength=500)
        self.byline.pack(fill="x")
        self.trust = ttk.Label(self, style="Muted.TLabel", wraplength=500)
        self.trust.pack(fill="x", pady=(2, 5))
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 5))
        self.button(actions, "↗", self.open_pdf, tooltip="Open original PDF", width=3)
        self.button(actions, "⧉", self.copy_citation, tooltip="Copy citation", width=3)
        nav = actions
        self.previous = self.button(nav, "‹", lambda: self.turn(-1), tooltip="Previous page", width=3)
        self.page_label = ttk.Label(nav, text="Full page", width=14, anchor="center")
        self.page_label.pack(side="left")
        self.next = self.button(nav, "›", lambda: self.turn(1), tooltip="Next page", width=3)
        self.view_toggle = self.button(actions, "📄 PDF", self.toggle_view, tooltip="Toggle between PDF view and extracted text", width=6)
        self.more_button = self.button(actions, "⋯", self.show_menu, tooltip="Paper actions and details", width=3)
        self.more_button.pack_forget()
        self.more_button.pack(side="right")
        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="Read full page", command=lambda: self.render("page"))
        self.menu.add_command(label="Read matched passage", command=lambda: self.render("passage"))
        if not review:
            self.menu.add_command(label="Review this paper", command=lambda: app.open_review(self.paper["id"], result=self.result, page=self.page) if self.paper else None)
        self.menu.add_separator()
        self.menu.add_command(label="Check DOI", command=self.verify)
        self.menu.add_command(label="Edit metadata…", command=lambda: app.edit_metadata(self.paper["id"]) if self.paper else None)
        self.menu.add_command(label="Assess a question…", command=lambda: app.assess(self.paper["id"]) if self.paper else None)
        self.menu.add_separator()
        self.menu.add_command(label="Paper details…", command=self.show_details)
        self.menu.add_command(label="Copy agent JSON", command=self.copy_json)
        self.menu.add_separator()
        self.menu.add_command(label="Embedded PDF", command=lambda: self.set_view("pdf"))
        self.menu.add_command(label="Extracted text", command=lambda: self.set_view("text"))
        self.menu.add_command(label="Zoom in (Ctrl + wheel)", command=lambda: self.pdf.set_zoom(self.pdf.zoom * 1.2))
        self.menu.add_command(label="Zoom out", command=lambda: self.pdf.set_zoom(self.pdf.zoom / 1.2))
        self.menu.add_command(label="Fit PDF to width", command=lambda: self.pdf.set_zoom(1.0))
        self.text = textbox(self, font=("Georgia", 12))
        self.text.tag_configure("match", background="#fff0bc")
        self.pdf = PDFViewer(self, self.pdf_error)
        self.detail_content = ""
        self.bind("<Configure>", self.resize)
        self.clear()

    def button(self, parent, text, command, tooltip=None, **options):
        if tooltip:
            options.setdefault("style", "Icon.TButton")
        button = ttk.Button(parent, text=text, command=command, **options)
        button.pack(side="left", padx=(0, 5))
        self.buttons.append(button)
        if tooltip:
            Tooltip(button, tooltip)
        return button

    def resize(self, event):
        for label in (self.title, self.byline, self.trust):
            label.configure(wraplength=max(220, event.width - 28))

    def clear(self, message="Select a paper to start reading"):
        self.paper = self.result = None
        self.title.configure(text=message)
        for label in (self.byline, self.trust):
            label.configure(text="")
        self.page_label.configure(text="Full page")
        self.detail_content = ""
        self.pdf.request = None
        self.pdf.pack_forget()
        self.text.master.pack(fill="both", expand=True)
        put_text(self.text, "")
        for button in self.buttons:
            button.configure(state="disabled")

    def load(self, paper_id, result=None, page=None):
        try:
            self.paper = self.app.library.show(paper_id)
            self.result = result
            self.query = result.get("query", self.app.searched_query) if result else ""
            self.page = page or (result["match"]["page"] if result else 1)
            for button in self.buttons:
                button.configure(state="normal")
            self.title.configure(text=self.paper["title"])
            # Keep long author lists in details so they cannot consume the reading area.
            authors = self.paper.get("authors") or "Author not recorded"
            self.byline.configure(text=f"{textwrap.shorten(authors, 110, placeholder='…')} · {self.paper.get('year') or 'Year not recorded'}")
            self.trust.configure(text=f"{APPROVAL_LABELS[self.paper['status']]} · {identity_label(self.paper)}")
            self.menu.entryconfigure(1, state="normal" if result else "disabled")
            report = self.paper.get("verification", {})
            lines = [f"Paper ID: {paper_id}", f"DOI: {self.paper.get('doi') or 'Not recorded'}",
                     f"Authors: {authors}", f"Decision: {self.paper.get('status_reason') or 'No decision recorded'}"]
            if report.get("reason"):
                lines.append(f"Identity check: {report['reason']}")
            if result:
                lines.append(f"Similarity: {result['score']:.4f} · lexical {result.get('lexical_score', 0):.3f} · semantic {result.get('semantic_score', 0):.3f}")
                lines.append("Retrieval similarity does not measure evidence quality.")
            self.detail_content = "\n".join(lines)
            self.render("page")
        except Exception as exc:
            self.clear("Could not load this paper")
            self.app.status.set(str(exc))

    def render(self, mode="page"):
        if not self.paper:
            return
        self.mode = mode
        if mode == "passage" and self.result:
            self.page = self.result["match"]["page"]
            content = self.result["match"]["text"]
        else:
            try:
                content = self.app.library.text(self.paper["id"], page=self.page)["text"]
            except KeyError:
                content = "No extracted text on this page. Open the PDF to read it."
            except Exception as exc:
                content = f"Could not read page text: {exc}"
        put_text(self.text, content)
        if self.view == "pdf":
            self.text.master.pack_forget()
            self.pdf.pack(fill="both", expand=True)
            passage = self.result["match"]["text"] if self.result and self.page == self.result["match"]["page"] else ""
            self.pdf.show(self.paper["pdf_path"], self.page, self.query, passage)
        else:
            self.pdf.request = None
            self.pdf.pack_forget()
            self.text.master.pack(fill="both", expand=True)
        self.page_label.configure(text=f"{'Passage' if mode == 'passage' else 'Page'} {self.page} / {self.paper['page_count']}")
        self.previous.configure(state="normal" if self.page > 1 else "disabled")
        self.next.configure(state="normal" if self.page < self.paper["page_count"] else "disabled")
        if self.result and self.app.highlight.get():
            from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
            terms = set(re.findall(r"\b\w{3,}\b", self.app.searched_query.lower())) - ENGLISH_STOP_WORDS
            for term in terms:
                start = "1.0"
                while True:
                    position = self.text.search(rf"\m{term}\M", start, stopindex="end", nocase=True, regexp=True)
                    if not position:
                        break
                    end = f"{position}+{len(term)}c"
                    self.text.tag_add("match", position, end)
                    start = end

    def show_menu(self):
        if self.paper:
            self.app.popup(self.menu, self.more_button)

    def toggle_view(self):
        new_view = "text" if self.view == "pdf" else "pdf"
        self.set_view(new_view)

    def update_view_button(self):
        if hasattr(self, "view_toggle"):
            self.view_toggle.configure(text="📄 PDF" if self.view == "pdf" else "📝 Text")

    def set_view(self, view):
        self.view = view
        self.update_view_button()
        self.render(self.mode)

    def pdf_error(self, message):
        self.view = "text"
        self.update_view_button()
        self.pdf.pack_forget()
        self.text.master.pack(fill="both", expand=True)
        self.app.status.set(f"PDF preview unavailable; showing extracted text. {message}")

    def show_details(self):
        if self.paper:
            win = tk.Toplevel(self.app.root)
            win.title("Paper details")
            win.geometry("720x480")
            put_text(textbox(win), self.detail_content)

    def turn(self, delta):
        if self.paper:
            self.page = min(self.paper["page_count"], max(1, self.page + delta))
            self.render("page")

    def open_pdf(self):
        if self.paper:
            self.app.open_pdf(self.paper["pdf_path"])

    def copy_citation(self):
        if self.paper:
            paper = self.app.library.show(self.paper["id"])
            message = "Citation copied" if paper["status"] == "accepted" else "Citation copied with an unreviewed label"
            self.app.copy(citation_text(paper, self.page), message)

    def copy_json(self):
        if self.paper:
            try:
                paper = self.app.library.show(self.paper["id"])
                payload = {"paper": paper, "page": self.page, "text": self.app.library.text(paper["id"], page=self.page)["text"],
                           "warning": "NEEDS REVIEW" if paper["status"] != "accepted" else None}
                self.app.copy(json.dumps(payload, indent=2, ensure_ascii=False), "Agent JSON copied")
            except Exception as exc:
                self.app.status.set(f"Could not export: {exc}")

    def verify(self):
        if self.paper:
            paper_id = self.paper["id"]
            self.app.run_job("Checking DOI…", lambda: self.app.library.verify(paper_id),
                             lambda _: self.app.after_change("Identity check saved. Your approval decision is unchanged."))


class LiteratureLibraryGUI:
    def __init__(self, root: tk.Tk, library: Library):
        self.root, self.library = root, library
        root.title("Literature Library")
        root.geometry("1280x860")
        root.minsize(1060, 720)
        self.messages = queue.Queue()
        self.busy = self.closed = False
        self.current_search_results, self.all_library_papers = [], []
        self.searched_query, self.searched_approved = "", False
        self.result_items, self.scan_paths = {}, {}
        self.deferred = set()
        self.review_target = self.undo_decision = None
        self.layout_path = library.root / "ui_preferences.json"
        try:
            self.layout = json.loads(self.layout_path.read_text(encoding="utf-8"))
            if not isinstance(self.layout, dict):
                self.layout = {}
        except (OSError, ValueError):
            self.layout = {}
        self.highlight = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Search your library, or open Review to work through new papers.")
        self.styles()
        self.build()
        self.refresh_library()
        root.bind("<Control-f>", lambda _: self.focus_search())
        root.bind("<Control-F>", lambda _: self.focus_search())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(60, self.poll)

    def styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        self.root.configure(bg="#f5f6f8")
        style.configure(".", font=("Segoe UI", 10), background="#f5f6f8", foreground="#223044")
        style.configure("TButton", font=("Segoe UI", 9), padding=(6, 3))
        style.configure("Icon.TButton", font=("Segoe UI Symbol", 11), padding=(4, 1))
        style.configure("Quiet.TButton", relief="flat", padding=(3, 2))
        style.configure("Primary.TButton", background="#235e70", foreground="white", padding=(7, 3))
        style.map("Primary.TButton", background=[("disabled", "#dde3e7"), ("active", "#194b5b")], foreground=[("disabled", "#7a8793")])
        style.configure("TNotebook", borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI", 9), padding=(12, 4))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")])
        style.configure("Title.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("ReaderTitle.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Muted.TLabel", foreground="#647182", font=("Segoe UI", 9))
        style.configure("Treeview", rowheight=34, background="white", fieldbackground="white", borderwidth=0)
        style.configure("Results.Treeview", rowheight=102, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", padding=7, font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#dfeef2")], foreground=[("selected", "#193d49")])

    def build(self):
        header = ttk.Frame(self.root, padding=(14, 5, 14, 5))
        header.pack(fill="x")
        ttk.Label(header, text="Literature Library", style="Title.TLabel").pack(side="left")
        self.counts = ttk.Label(header, style="Muted.TLabel")
        self.counts.pack(side="right")
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10)
        self.tab_search, self.tab_review, self.tab_library, self.tab_add = [ttk.Frame(self.notebook, padding=8) for _ in range(4)]
        for tab, label in zip((self.tab_search, self.tab_review, self.tab_library, self.tab_add), ("Search", "Review", "Library", "Add PDFs")):
            self.notebook.add(tab, text=label)
        self.build_search()
        self.build_review()
        self.build_library()
        self.build_add()
        bottom = ttk.Frame(self.root, padding=(14, 3))
        bottom.pack(side="bottom", before=self.notebook, fill="x")
        ttk.Label(bottom, textvariable=self.status, style="Muted.TLabel", wraplength=930).pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=100)
        self.progress.pack(side="right")

    def tree(self, parent, columns=(), results=False):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, height=4, show="tree" if results else "headings", selectmode="browse",
                            style="Results.Treeview" if results else "Treeview")
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        return tree

    def build_search(self):
        row = ttk.Frame(self.tab_search)
        row.pack(fill="x")
        self.search_entry = ttk.Combobox(row, font=("Segoe UI", 11))
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.search_entry.bind("<Return>", lambda _: self.on_search())
        self.btn_search = ttk.Button(row, text="⌕", width=4, style="Primary.TButton", command=self.on_search)
        self.btn_search.pack(side="left")
        Tooltip(self.btn_search, "Search (Enter)")
        Tooltip(self.search_entry, "Search by question or phrase · Ctrl+F")
        self.scope = tk.StringVar(value="All searchable papers")
        scope = ttk.Combobox(row, textvariable=self.scope, state="readonly", width=23, values=("All searchable papers", "Approved papers only"))
        scope.pack(side="left", padx=(10, 0))
        scope.bind("<<ComboboxSelected>>", lambda _: self.on_search() if self.search_entry.get().strip() else None)
        self.limit = tk.StringVar(value="16")
        self.search_options = tk.Menu(self.root, tearoff=False)
        limits = tk.Menu(self.search_options, tearoff=False)
        for value in (8, 16, 30, 60):
            limits.add_radiobutton(label=f"{value} passages", variable=self.limit, value=str(value))
        self.search_options.add_cascade(label="Passages to retrieve", menu=limits)
        self.search_options.add_checkbutton(label="Highlight query words", variable=self.highlight,
                                            command=lambda: self.search_reader.render(self.search_reader.mode))
        self.search_options.add_separator()
        self.search_options.add_command(label="Reset panel widths", command=self.reset_panes)
        options_button = ttk.Button(row, text="⋯", width=3, style="Icon.TButton", command=lambda: self.popup(self.search_options, options_button))
        options_button.pack(side="left", padx=(6, 0))
        Tooltip(options_button, "Search options")
        self.search_summary = ttk.Label(self.tab_search, text="Search by question or phrase. Drag the centre divider to resize panels.", style="Muted.TLabel", wraplength=980)
        self.search_summary.pack(anchor="w", pady=(4, 5))
        self.show_all_button = ttk.Button(self.tab_search, text="Search all searchable papers", command=self.search_all)
        self.search_panes = self.panes(self.tab_search, "search")
        self.search_panes.pack(fill="both", expand=True)
        left = ttk.Frame(self.search_panes, width=400)
        self.search_panes.add(left, minsize=260, stretch="always")
        self.search_tree = self.tree(left, results=True)
        self.search_tree.column("#0", width=385, minwidth=280)
        self.search_tree.bind("<<TreeviewSelect>>", self.on_search_select)
        self.search_tree.bind("<Double-1>", lambda _: self.search_reader.open_pdf())
        self.search_tree.bind("<Configure>", lambda _: self.wrap_results())
        self.search_reader = Reader(self.search_panes, self)
        self.search_panes.add(self.search_reader, minsize=380, stretch="always")

    def build_review(self):
        self.review_summary = ttk.Label(self.tab_review, style="Muted.TLabel", wraplength=980)
        self.review_summary.pack(anchor="w", pady=(0, 5))
        paned = self.review_panes = self.panes(self.tab_review, "review")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned, width=330)
        paned.add(left, minsize=240, stretch="always")
        self.review_tree = self.tree(left, results=True)
        self.review_tree.column("#0", width=315, minwidth=260)
        self.review_tree.bind("<<TreeviewSelect>>", self.select_review)
        self.review_tree.bind("<Configure>", lambda _: self.wrap_queue())
        self.review_reader = Reader(paned, self, review=True)
        paned.add(self.review_reader, minsize=380, stretch="always")
        decision = ttk.Frame(self.tab_review, padding=(0, 5, 0, 0))
        decision.pack(side="bottom", before=paned, fill="x")
        ttk.Label(decision, text="Note", style="Muted.TLabel").pack(side="left")
        self.reason = ttk.Entry(decision)
        self.reason.pack(side="left", fill="x", expand=True, padx=8)
        Tooltip(self.reason, "Optional note saved with your decision")
        self.approve = ttk.Button(decision, text="✓ Approve", style="Primary.TButton", command=lambda: self.decide("accepted"))
        self.approve.pack(side="left")
        Tooltip(self.approve, "Approve this paper and move to the next")
        self.exclude = ttk.Button(decision, text="× Exclude", command=lambda: self.decide("rejected"))
        self.exclude.pack(side="left", padx=6)
        Tooltip(self.exclude, "Exclude this paper and move to the next")
        self.later = ttk.Button(decision, text="›", width=3, command=self.defer)
        self.later.pack(side="left")
        Tooltip(self.later, "Later — set aside for this session")
        self.queue_menu = tk.Menu(self.root, tearoff=False)
        self.queue_menu.add_command(label="Undo last decision", command=self.undo, state="disabled")
        self.queue_menu.add_command(label="Bring back deferred papers", command=self.restore_deferred)
        self.queue_menu.add_command(label="Reset panel widths", command=self.reset_panes)
        queue_button = ttk.Button(decision, text="⋯", width=3, style="Icon.TButton", command=lambda: self.popup(self.queue_menu, queue_button))
        queue_button.pack(side="left", padx=(6, 0))
        Tooltip(queue_button, "Review queue options")

    def build_library(self):
        row = ttk.Frame(self.tab_library)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text="Your papers", style="Title.TLabel").pack(side="left", padx=(0, 16))
        self.catalog_query = ttk.Entry(row)
        self.catalog_query.pack(side="left", fill="x", expand=True)
        self.catalog_query.bind("<KeyRelease>", lambda _: self.filter_catalog())
        self.catalog_scope = tk.StringVar(value="All papers")
        scope = ttk.Combobox(row, textvariable=self.catalog_scope, state="readonly", width=22,
                            values=("All papers", "Approved", "Awaiting your review", "Excluded"))
        scope.pack(side="left", padx=8)
        scope.bind("<<ComboboxSelected>>", lambda _: self.filter_catalog())
        ttk.Button(row, text="Refresh", command=self.after_change).pack(side="left")
        self.catalog_tree = self.tree(self.tab_library, columns=("title", "year", "approval", "identity"))
        for column, label, width in (("title", "Paper", 530), ("year", "Year", 70), ("approval", "Your decision", 175), ("identity", "Identity check", 220)):
            self.catalog_tree.heading(column, text=label)
            self.catalog_tree.column(column, width=width, minwidth=60, stretch=column == "title")
        self.catalog_tree.bind("<Double-1>", lambda _: self.review_catalog())
        row = ttk.Frame(self.tab_library, padding=(0, 10, 0, 0))
        row.pack(fill="x")
        ttk.Button(row, text="Read / review selected", style="Primary.TButton", command=self.review_catalog).pack(side="left")
        ttk.Button(row, text="Open PDF", command=self.open_catalog_pdf).pack(side="left", padx=8)
        ttk.Label(row, text="Filter by title, author, DOI or paper ID.", style="Muted.TLabel").pack(side="left")

    def build_add(self):
        ttk.Label(self.tab_add, text="Add papers to your library", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.tab_add, text="Choose PDFs. They become searchable immediately and join your review queue.", style="Muted.TLabel").pack(anchor="w", pady=(6, 12))
        ttk.Button(self.tab_add, text="Choose PDFs…", style="Primary.TButton", command=self.choose_pdfs).pack(anchor="w")
        advanced = Disclosure(self.tab_add, "Import options & folder scan")
        advanced.pack(fill="x", pady=14)
        ttk.Label(advanced.body, text="Optional research question for screening").pack(anchor="w")
        self.import_question = ttk.Entry(advanced.body)
        self.import_question.pack(fill="x", pady=(4, 8))
        ttk.Button(advanced.body, text="Scan References/PDFs", command=self.scan_unindexed).pack(anchor="w")
        self.scan_tree = self.tree(advanced.body, columns=("name", "folder"))
        self.scan_tree.configure(selectmode="extended")
        self.scan_tree.heading("name", text="PDF")
        self.scan_tree.heading("folder", text="Folder")
        self.scan_tree.column("name", width=650)
        self.scan_tree.column("folder", width=300)
        ttk.Button(advanced.body, text="Add selected PDFs", command=self.import_selected).pack(anchor="w", pady=6)
        self.import_summary = ttk.Label(self.tab_add, text="Folder scanning is optional. You can select several PDFs in the file picker.", style="Muted.TLabel", wraplength=950)
        self.import_summary.pack(anchor="w")

    def focus_search(self):
        self.notebook.select(self.tab_search)
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def run_job(self, label, work, finish):
        if self.busy:
            self.status.set("Please wait for the current operation to finish.")
            return False
        self.busy = True
        self.status.set(label)
        self.btn_search.configure(state="disabled")
        self.progress.start(12)
        def worker():
            try:
                self.messages.put((finish, work(), None))
            except Exception as exc:
                self.messages.put((finish, None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        return True

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                finish, value, error = self.messages.get_nowait()
                self.busy = False
                self.progress.stop()
                self.btn_search.configure(state="normal")
                if error:
                    self.status.set(f"Could not complete operation: {error}")
                    messagebox.showerror("Operation failed", error, parent=self.root)
                else:
                    try:
                        finish(value)
                    except Exception as exc:
                        self.status.set(f"Could not display result: {exc}")
                        messagebox.showerror("Display failed", str(exc), parent=self.root)
        except queue.Empty:
            pass
        self.poll_id = self.root.after(60, self.poll)

    def close(self):
        self.closed = True
        self.save_layout()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()

    @staticmethod
    def popup(menu, button):
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()

    def panes(self, parent, name):
        pane = tk.PanedWindow(parent, orient="horizontal", sashwidth=9, sashrelief="raised", showhandle=True,
                              handlesize=9, handlepad=70, opaqueresize=True, borderwidth=0, background="#dce3e8")
        pane.bind("<Map>", lambda _: self.restore_pane(pane, name))
        pane.bind("<ButtonRelease-1>", lambda _: self.save_layout())
        return pane

    def restore_pane(self, pane, name):
        def place():
            if self.closed or not pane.winfo_exists() or pane.winfo_width() < 10:
                return
            ratio = self.layout.get(name, 0.40 if name == "search" else 0.33)
            if not isinstance(ratio, (int, float)) or not 0.2 <= ratio <= 0.8:
                ratio = 0.40 if name == "search" else 0.33
            pane.sash_place(0, int(pane.winfo_width() * ratio), 0)
        self.root.after_idle(place)

    def save_layout(self):
        for name, pane in (("search", self.search_panes), ("review", self.review_panes)):
            if pane.winfo_width() > 10:
                self.layout[name] = pane.sash_coord(0)[0] / pane.winfo_width()
        try:
            self.layout_path.write_text(json.dumps(self.layout, indent=2), encoding="utf-8")
        except OSError:
            pass

    def reset_panes(self):
        self.layout = {}
        self.restore_pane(self.search_panes, "search")
        self.restore_pane(self.review_panes, "review")

    def on_search(self):
        query = self.search_entry.get().strip()
        if not query:
            self.status.set("Enter a question or phrase to search.")
            self.search_entry.focus_set()
            return
        try:
            limit = int(self.limit.get())
            if not 1 <= limit <= 60:
                raise ValueError
        except ValueError:
            self.status.set("Choose a passage limit between 1 and 60 in Search options.")
            return
        approved = self.scope.get() == "Approved papers only"
        self.run_job("Searching…", lambda: self.library.search(query, limit=limit, verified_only=approved),
                     lambda results: self.display_results(results, query, approved))

    def search_all(self):
        self.scope.set("All searchable papers")
        self.on_search()

    def display_results(self, results, query, approved=False):
        self.current_search_results, self.searched_query, self.searched_approved = results, query, approved
        self.search_tree.delete(*self.search_tree.get_children())
        self.result_items.clear()
        self.search_reader.clear()
        self.show_all_button.pack_forget()
        history = list(self.search_entry.cget("values"))
        self.search_entry.configure(values=[query] + [q for q in history if q != query][:11])
        if not results:
            if approved:
                message = "No matches in approved papers. Unreviewed papers may still contain useful passages."
                self.show_all_button.pack(before=self.search_panes, anchor="w", pady=(0, 8))
            else:
                message = "No matching passages found. Try a different phrase or add a relevant PDF."
            self.search_summary.configure(text=message)
            self.search_reader.clear("No matching passages")
            put_text(self.search_reader.text, message)
            self.status.set("Search complete")
            return
        groups = group_results(results)
        self.search_summary.configure(text=f"{len(groups)} papers · {sum(map(len, groups))} matching pages · “{query}”")
        for group in groups:
            parent = self.search_tree.insert("", "end", open=False)
            self.result_items[parent] = (group[0], True)
            for result in group:
                child = self.search_tree.insert(parent, "end")
                self.result_items[child] = (result, False)
        self.wrap_results()
        self.search_tree.selection_set(self.search_tree.get_children()[0])
        self.on_search_select()
        self.status.set("Search complete. Select a passage to read its full page.")

    def wrap_results(self):
        width = max(26, (self.search_tree.winfo_width() - 62) // 7)
        for item, (result, parent) in self.result_items.items():
            match = result["match"]
            if parent:
                title = "\n".join(textwrap.wrap(match["title"], width, max_lines=2, placeholder="…"))
                preview = "\n".join(textwrap.wrap(" ".join(match["text"].split()), width, max_lines=2, placeholder="…"))
                pages = len(self.search_tree.get_children(item))
                text = f"{title}\n{APPROVAL_LABELS[match['status']]} · {pages} matching {'page' if pages == 1 else 'pages'}\n{preview}"
            else:
                preview = "\n".join(textwrap.wrap(" ".join(match["text"].split()), width - 3, max_lines=3, placeholder="…"))
                text = f"Page {match['page']}\n{preview}"
            self.search_tree.item(item, text=text)

    def on_search_select(self, _=None):
        selected = self.search_tree.selection()
        if selected and selected[0] in self.result_items:
            result = self.result_items[selected[0]][0]
            self.search_reader.load(result["match"]["paper_id"], result)

    def refresh_library(self):
        self.all_library_papers = self.library.list_papers()
        counts = {status: sum(p["status"] == status for p in self.all_library_papers) for status in APPROVAL_LABELS}
        self.counts.configure(text=f"{len(self.all_library_papers)} papers · {counts['accepted']} approved · {counts['needs_review']} awaiting review")
        self.notebook.tab(self.tab_review, text=f"Review ({counts['needs_review']})")
        self.filter_catalog()
        self.refresh_queue()

    def filter_catalog(self):
        query, scope = self.catalog_query.get().strip().lower(), self.catalog_scope.get()
        selected = self.catalog_tree.selection()
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        for paper in self.all_library_papers:
            if scope != "All papers" and APPROVAL_LABELS[paper["status"]] != scope:
                continue
            if query and query not in " ".join(str(paper.get(key) or "") for key in ("id", "title", "authors", "doi")).lower():
                continue
            self.catalog_tree.insert("", "end", iid=paper["id"], values=(paper["title"], paper.get("year") or "—", APPROVAL_LABELS[paper["status"]], identity_label(paper)))
        if selected and self.catalog_tree.exists(selected[0]):
            self.catalog_tree.selection_set(selected[0])

    def refresh_queue(self, prefer=None):
        previous = prefer or (self.review_reader.paper or {}).get("id")
        self.review_tree.delete(*self.review_tree.get_children())
        self.queue_papers = [p for p in self.all_library_papers if p["status"] == "needs_review" and p["id"] not in self.deferred]
        if self.review_target:
            target = next((p for p in self.all_library_papers if p["id"] == self.review_target), None)
            if target and target not in self.queue_papers:
                self.queue_papers.insert(0, target)
        for paper in self.queue_papers:
            self.review_tree.insert("", "end", iid=paper["id"])
        self.wrap_queue()
        self.review_summary.configure(text=f"{len(self.queue_papers)} papers in this queue · {len(self.deferred)} set aside this session. Inspect the source before approving.")
        children = self.review_tree.get_children()
        for button in (self.approve, self.exclude, self.later):
            button.configure(state="normal" if children else "disabled")
        if children:
            choice = previous if previous in children else children[0]
            self.review_tree.selection_set(choice)
            self.review_tree.see(choice)
            self.select_review()
        else:
            self.review_reader.clear("No papers waiting in this queue")

    def wrap_queue(self):
        width = max(26, (self.review_tree.winfo_width() - 42) // 7)
        for paper in getattr(self, "queue_papers", []):
            title = "\n".join(textwrap.wrap(paper["title"], width, max_lines=3, placeholder="…"))
            self.review_tree.item(paper["id"], text=f"{title}\n{identity_label(paper)}")

    def select_review(self, _=None):
        selected = self.review_tree.selection()
        if selected:
            if getattr(self, "review_target_context", None) and selected[0] == self.review_target:
                res, pg = self.review_target_context
                self.review_target_context = None
                self.reason.delete(0, "end")
                self.review_reader.load(selected[0], res, pg)
            elif not self.review_reader.paper or selected[0] != self.review_reader.paper["id"]:
                self.reason.delete(0, "end")
                self.review_reader.load(selected[0])

    def open_review(self, paper_id, result=None, page=None):
        self.review_target = paper_id
        self.deferred.discard(paper_id)
        if result:
            self.review_target_context = (result, page)
            self.review_reader.view = self.search_reader.view
            self.review_reader.update_view_button()
        else:
            self.review_target_context = None
        self.refresh_queue(prefer=paper_id)
        self.notebook.select(self.tab_review)
        if result and (not self.review_reader.paper or self.review_reader.paper["id"] != paper_id or self.review_reader.page != (page or result["match"]["page"])):
            self.review_reader.load(paper_id, result, page)

    def decide(self, status):
        if self.busy:
            self.status.set("Wait for the current operation before making a decision.")
            return
        paper = self.review_reader.paper
        if not paper:
            return
        try:
            previous = self.library.show(paper["id"])
            reason = self.reason.get().strip() or ("Approved by researcher after inspecting the source." if status == "accepted" else "Excluded by researcher.")
            self.library.decide(paper["id"], status, reason)
        except Exception as exc:
            self.status.set(f"Decision was not saved: {exc}")
            return
        self.undo_decision = (paper["id"], previous["status"], previous["status_reason"])
        self.queue_menu.entryconfigure(0, state="normal")
        self.review_target = None
        self.reason.delete(0, "end")
        self.after_change(f"{APPROVAL_LABELS[status]}: {paper['title']}")

    def defer(self):
        if self.review_reader.paper:
            self.deferred.add(self.review_reader.paper["id"])
            self.review_target = None
            self.refresh_queue()
            self.status.set("Set aside for this session. The paper's approval status has not changed.")

    def restore_deferred(self):
        self.deferred.clear()
        self.refresh_queue()

    def undo(self):
        if self.undo_decision and not self.busy:
            paper_id, status, reason = self.undo_decision
            try:
                self.library.decide(paper_id, status, reason)
            except Exception as exc:
                self.status.set(f"Could not undo: {exc}")
                return
            self.undo_decision = None
            self.queue_menu.entryconfigure(0, state="disabled")
            self.after_change("Previous decision restored")
            self.open_review(paper_id)

    def after_change(self, message="Library refreshed"):
        self.refresh_library()
        papers, refreshed = {p["id"]: p for p in self.all_library_papers}, []
        for result in self.current_search_results:
            paper = papers.get(result["match"]["paper_id"])
            if not paper or paper["status"] == "rejected" or (self.searched_approved and paper["status"] != "accepted"):
                continue
            for key in ("title", "authors", "year", "doi", "status"):
                result["match"][key] = paper.get(key)
            result["warning"] = "NEEDS REVIEW" if paper["status"] == "needs_review" else None
            result["identity_verified"] = bool(paper.get("verification", {}).get("verified"))
            refreshed.append(result)
        if self.current_search_results:
            self.display_results(refreshed, self.searched_query, self.searched_approved)
        if self.review_reader.paper:
            self.review_reader.load(self.review_reader.paper["id"], self.review_reader.result, self.review_reader.page)
        self.status.set(message)

    def review_catalog(self):
        selected = self.catalog_tree.selection()
        if selected:
            self.open_review(selected[0])
        else:
            self.status.set("Select a paper first.")

    def open_catalog_pdf(self):
        selected = self.catalog_tree.selection()
        if selected:
            self.open_pdf(self.library.show(selected[0])["pdf_path"])

    def open_pdf(self, path):
        try:
            if not Path(path).is_file():
                raise FileNotFoundError(path)
            os.startfile(path)
        except Exception as exc:
            self.status.set(f"Could not open PDF: {exc}")

    def copy(self, content, message):
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status.set(message)

    def edit_metadata(self, paper_id):
        paper = self.library.show(paper_id)
        win = tk.Toplevel(self.root)
        win.title("Review metadata corrections")
        win.geometry("810x610")
        win.transient(self.root)
        win.grab_set()
        frame = ttk.Frame(win, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Check the fields against the original paper", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Changes are audited. A changed title or DOI requires a fresh identity check.", style="Muted.TLabel", wraplength=740).pack(anchor="w", pady=8)
        entries = {}
        for key, label in (("title", "Title"), ("authors", "Authors"), ("year", "Year"), ("venue", "Journal / source"), ("doi", "DOI")):
            ttk.Label(frame, text=label).pack(anchor="w", pady=(5, 0))
            entry = ttk.Entry(frame)
            entry.insert(0, str(paper.get(key) or ""))
            entry.pack(fill="x", pady=(2, 0))
            entries[key] = entry
        report = paper.get("verification", {})
        note = ttk.Label(frame, style="Muted.TLabel", wraplength=740)
        note.pack(fill="x", pady=10)
        def suggestions():
            for key in ("title", "authors", "year", "venue"):
                if report.get(f"crossref_{key}"):
                    entries[key].delete(0, "end")
                    entries[key].insert(0, str(report[f"crossref_{key}"]))
            note.configure(text="Suggestions filled in for review. Check that they describe this PDF before saving.")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=10)
        if report.get("crossref_title"):
            ttk.Button(row, text="Fill Crossref suggestions", command=suggestions).pack(side="left")
        ttk.Button(row, text="Open original PDF", command=lambda: self.open_pdf(paper["pdf_path"])).pack(side="left", padx=6)
        def save():
            if self.busy:
                note.configure(text="Wait for the current operation before saving.")
                return
            try:
                values = {key: entry.get().strip() for key, entry in entries.items()}
                values["year"] = int(values["year"]) if values["year"] else None
                self.library.update_metadata(paper_id, **values)
            except Exception as exc:
                note.configure(text=f"Could not save: {exc}")
                return
            win.destroy()
            self.after_change("Metadata correction saved")
        ttk.Button(row, text="Save corrections", style="Primary.TButton", command=save).pack(side="right")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=6)

    def assess(self, paper_id):
        win = tk.Toplevel(self.root)
        win.title("Find passages for a question")
        win.geometry("860x650")
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="What do you want to check in this paper?", style="Title.TLabel").pack(anchor="w")
        query = ttk.Entry(frame)
        query.insert(0, self.searched_query)
        query.pack(fill="x", pady=10)
        ttk.Label(frame, text="Screening uses retrieval similarity. It does not establish quality or approve a paper.", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        output = textbox(frame)
        def finish(report):
            self.status.set("Screening complete; inspect full pages before deciding.")
            if not win.winfo_exists():
                return
            label = {"accept": "Potentially relevant", "review": "Inspect further", "reject": "Weak match to this question"}[report["recommended_verdict"]]
            content = f"{label}\n\n"
            for item in report["evidence"]:
                match = item["match"]
                content += f"Page {match['page']}\n\n{match['text']}\n\n"
            put_text(output, content)
        def run():
            question = query.get().strip()
            if question:
                self.run_job("Finding passages in this paper…", lambda: self.library.assess(paper_id, question), finish)
        ttk.Button(frame, text="Find passages", style="Primary.TButton", command=run).pack(anchor="w", pady=10)
        query.bind("<Return>", lambda _: run())

    def choose_pdfs(self):
        paths = filedialog.askopenfilenames(parent=self.root, title="Choose PDFs", filetypes=[("PDF files", "*.pdf")],
                                           initialdir=str(self.library.root.parent / "References" / "PDFs"))
        if paths:
            self.import_pdfs([Path(path) for path in paths])

    def scan_unindexed(self):
        def work():
            with self.library.connect() as conn:
                existing = {row[0] for row in conn.execute("SELECT sha256 FROM papers")}
            from .core import file_hash
            folder = self.library.root.parent / "References" / "PDFs"
            return [path for path in sorted(folder.rglob("*.pdf")) if path.is_file() and file_hash(path) not in existing]
        def finish(paths):
            self.scan_tree.delete(*self.scan_tree.get_children())
            self.scan_paths = {}
            for path in paths:
                item = self.scan_tree.insert("", "end", values=(path.name, path.parent.name))
                self.scan_paths[item] = path
            self.import_summary.configure(text=f"{len(paths)} PDFs not yet indexed. Select files with Ctrl/Shift, then Add selected PDFs.")
            self.status.set("Folder scan complete")
        self.run_job("Scanning PDFs…", work, finish)

    def import_selected(self):
        paths = [self.scan_paths[item] for item in self.scan_tree.selection()]
        if paths:
            self.import_pdfs(paths)
        else:
            self.status.set("Choose PDFs or scan the folder, then select files to add.")

    def import_pdfs(self, paths):
        question = self.import_question.get().strip() or None
        def work():
            added, duplicates, failures = 0, 0, []
            for path in paths:
                try:
                    result = self.library.ingest(path, question=question)
                    duplicates += int(bool(result.get("duplicate")))
                    added += int(not result.get("duplicate"))
                except Exception as exc:
                    failures.append(f"{path.name}: {exc}")
            return added, duplicates, failures
        def finish(result):
            added, duplicates, failures = result
            summary = f"Added {added} papers · {duplicates} duplicates skipped · {len(failures)} errors"
            self.import_summary.configure(text=summary + ("\n" + "\n".join(failures[:3]) if failures else ""))
            self.after_change(summary)
            self.scan_tree.delete(*self.scan_tree.get_children())
        self.run_job(f"Adding {len(paths)} PDFs…", work, finish)


def run_gui(library: Library):
    library.initialize()
    root = tk.Tk()
    LiteratureLibraryGUI(root, library)
    root.mainloop()

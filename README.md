# semantic-search-research (Local Literature Library)

A terminal-first, evidence-grounded literature library and semantic retrieval engine designed for autonomous AI agents and human researchers.

- **For AI Agents (Terminal / Scripts)**: Returns clean text and structured JSON (`--json`) with exact paper IDs, DOIs, page numbers, and verified evidence status.
- **For Human Researchers (Desktop GUI)**: Native Tkinter interface featuring an embedded PDF viewer with query/passage keyword highlighting, target match scrolling, 1-click toggle between embedded PDF and extracted text (`📄 PDF` / `📝 Text`), and seamless Search-to-Review workflow.

## Quick Launch & Desktop Interface

- **Standalone / Module**: Run `python lit.py ui` or execute `launch_ui.bat`.
- **From Research Root**: Run `python lit.py ui` or `launch_ui.bat`.
- **Module execution**: `python -m literature_library ui`

- **Search:** enter a descriptive phrase or question and press Enter. Results are
  grouped by paper; expand a paper's arrow to see individual matching pages.
  Selecting a result opens the full page. The arrow icon opens the original PDF;
  the overlapping-squares icon copies a citation. Hover over icons for labels.
- **Review:** inspect one paper, optionally enter a decision note, then choose
  **Approve** or **Exclude** to advance. The `›` button sets a paper aside for the
  current session. The queue's three-dot menu contains undo and deferred papers.
- **Library:** filter by title, author, DOI, paper ID or your decision. Double-click
  a paper to read/review it, including previously approved or excluded papers.
- **Add PDFs:** choose one or several PDFs. Optional screening and a content-hash
  folder scan are under **Import options & folder scan**.

The three-dot menu beside the search box contains the result limit and highlighting.
Each reader's three-dot menu contains matched-passage mode, DOI checks, metadata
corrections, question screening, full metadata, and agent JSON export.

Drag the visible divider between the list and reader in Search or Review to resize
them. Widths are saved in `LiteratureLibrary/ui_preferences.json`. Use **Reset panel
widths** in a three-dot menu to restore defaults. Ctrl+F focuses the search box.

## Approval and identity are independent

The UI displays your decision separately from DOI identity. Verification never
changes an existing approval, exclusion, or decision reason. Missing DOI, title
mismatch, Crossref not-found, and unavailable checks have different labels.
An accepted record is not automatically displayed as DOI-matched.

The metadata editor can fill saved Crossref suggestions for you to inspect before
saving. Corrections are audited; changing a title or DOI clears its identity check,
while preserving the researcher's decision. Existing metadata is not bulk rewritten.
Unapproved citation exports carry an explicit unreviewed label. Screening labels
describe relevance suggestions; the agent API retains its legacy confidence field
but explicitly identifies it as an uncalibrated retrieval heuristic.

## Retrieval performance and checks

The TF-IDF/LSA algorithm and weights are retained. A bounded, content-keyed JSON
cache reuses repeated query computations across launches. New queries still fit
the original representation; this is not a new embedding index. Metadata and trust
states are read from SQLite on every search. Zero-similarity candidates are no
longer presented as matches. Full pages are read from the original extracted text,
avoiding duplicate overlap from search chunks.

From `code/`, using the project interpreter:

```powershell
& Starting_Model/.venv/Scripts/python.exe -m unittest discover -s literature_library/tests -v
& Starting_Model/.venv/Scripts/python.exe ../lit.py benchmark literature_library/benchmark.library.json
& Starting_Model/.venv/Scripts/python.exe literature_library/tests/check_retrieval.py
& Starting_Model/.venv/Scripts/python.exe literature_library/tests/capture_ui.py
```

The real-library benchmark has three discovery queries with concrete paper IDs;
it is a regression starting point, not a broad assessment of retrieval quality.
The comparison script checks exact top-five scores against the prior algorithm.
The optional visual script requires Pillow and captures only this app's windows.
Reports/screenshots are in `docs/literature-ui/` under the research root. UI decision
tests use temporary libraries and never accept/reject real research papers.

This terminal-first system keeps one canonical paper record, extracts complete
page-aware text, verifies DOI identity through Crossref, records contextual
relevance assessments, and searches passages using hybrid lexical and
latent-semantic retrieval.

Data is stored under `LiteratureLibrary/` by default. The original input PDF is
copied, never deleted. Papers begin as `needs_review`; only the researcher makes
the final acceptance decision.

## Quick start

```powershell
python lit.py init
python lit.py ingest "C:\path\paper.pdf" --question "Does this support multi-site PV forecasting?"
python lit.py verify paper_123456789abc
python lit.py decide paper_123456789abc accepted --reason "Identity checked; relevant evidence confirmed"
python lit.py search "cloud-front movement between PV sites"
python lit.py text paper_123456789abc --page 8
python lit.py search "temporal validation leakage" --verified-only --json
```

Complete extracted text is in `LiteratureLibrary/text/`. Search returns the
matched passage, page, paper name, score, trust state, DOI, and paper ID.

## Agent screening protocol

For relevance and insight screening, an agent should:

1. Run `search` or `assess` using the explicit research question.
2. Inspect the leading passages and surrounding pages with `text --page`.
3. Report relevance, findings, method, limitations, and contrary evidence.
4. Cite the paper ID and page for every substantive statement.
5. Treat `NEEDS REVIEW` evidence as untrusted and never silently recommend it
   for citation.
6. Use `decide` only after the researcher confirms the final status.

## Lifecycle

- `needs_review`: visible in normal search with a warning.
- `accepted`: researcher-approved evidence; included by `--verified-only`.
- `rejected`: retained in the registry and audit history, excluded from search.
- `purge`: irreversible removal requiring `--confirm` with the exact paper ID.

## Benchmark format

Copy `benchmark.example.json`, replace the placeholders with real accepted paper
IDs and expected phrases, then run `python lit.py benchmark benchmark.json`.

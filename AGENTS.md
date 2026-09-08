# AGENTS.md — Literature Library & Grounded Research Protocol

See root documentation: [`../../AGENTS.md`](file:///c:/Users/OEM/Documents/Studies/Honours/ITRI671%20Research/AGENTS.md)

## Summary for Agents in `literature_library/`

When working directly in or around this package:

- Entry point for users/agents: [`../../lit.py`](file:///c:/Users/OEM/Documents/Studies/Honours/ITRI671%20Research/lit.py)
- Package root: [`./core.py`](file:///c:/Users/OEM/Documents/Studies/Honours/ITRI671%20Research/code/literature_library/core.py), [`./cli.py`](file:///c:/Users/OEM/Documents/Studies/Honours/ITRI671%20Research/code/literature_library/cli.py)
- Test suite: `python -m unittest discover -s tests` (run from `code/` to prevent shadowing python stdlib `code`)
- Data storage: [`../../LiteratureLibrary/`](file:///c:/Users/OEM/Documents/Studies/Honours/ITRI671%20Research/LiteratureLibrary)

### Key Agent Command Syntax
```powershell
# From research root:
python lit.py search "<query>" --verified-only --json
python lit.py text <paper_id> --page <page_number> --json
python lit.py assess <paper_id> --question "<research question>" --json
python lit.py verify <paper_id> --json
python lit.py logs [--date YYYY-MM-DD] --json
```

### Search Engine Diagnostics & Logs
See [`ADMIN.md`](file:///c:/Users/OEM/Documents/Studies/Honours/ITRI671%20Research/code/literature_library/ADMIN.md) for diagnosing search results, interpreting daily JSON logs, and tuning retrieval parameters.

### Empty Search Policy
If a query yields no verified matches (`[]`), agents must:
1. Alert the user that no local verified evidence exists.
2. Never hallucinate fake references.
3. Recommend searching Google Scholar / IEEE / ScienceDirect / arXiv.
4. Provide structured search keyword suggestions.
5. Prompt the user to ingest downloaded PDFs using `lit.py ingest`.

"""Human-facing labels kept separate from the agent search contract."""
from collections import OrderedDict

APPROVAL_LABELS = {"accepted": "Approved", "needs_review": "Awaiting your review", "rejected": "Excluded"}


def identity_label(paper):
    report = paper.get("verification") or {}
    if report.get("verified"):
        return "DOI matched"
    if not paper.get("doi"):
        return "No DOI recorded"
    state, reason = report.get("state"), report.get("reason", "").lower()
    if state == "mismatch" or "title metadata needs review" in reason:
        return "Title needs checking"
    if state == "not_found" or "http 404" in reason:
        return "DOI not found in Crossref"
    if state == "unavailable" or "unavailable" in reason or "http" in reason:
        return "Check unavailable — retry later"
    return "Identity not checked"


def group_results(results):
    """Preserve ranking, grouping papers and removing duplicate page previews."""
    groups, seen = OrderedDict(), set()
    for result in results:
        match = result["match"]
        key = (match["paper_id"], match["page"])
        if key not in seen:
            seen.add(key)
            groups.setdefault(match["paper_id"], []).append(result)
    return list(groups.values())


def citation_text(paper, page):
    citation = f"({paper.get('authors') or 'Unknown author'}, {paper.get('year') or 'n.d.'}, {paper.get('id') or paper.get('paper_id')}, p. {page})"
    if paper.get("status") != "accepted":
        return "[UNREVIEWED — inspect and approve before citing] " + citation
    return citation

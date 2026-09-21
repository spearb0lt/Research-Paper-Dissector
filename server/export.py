"""Getting extracted data back out.

The point of extracting a thirteen column ablation table is undermined if the
only way to use it is to retype it. Three formats, each the one a reader would
otherwise produce by hand:

* **CSV** for a table, so it opens in a spreadsheet.
* **Markdown** for an analysis, with every citation resolved from an excerpt
  number into a page reference, because "[3]" means nothing once the answer
  has left the screen it was written on.
* **BibTeX** for the paper itself, because the next thing anyone does with a
  paper they trust is cite it.

Everything here reads from stored rows. Nothing re-parses, nothing calls a
model, and any of it can be produced long after the fact.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from .db import repo
from .util import slugify


def table_csv(element: dict[str, Any]) -> str:
    """One table as CSV.

    Written with the csv module rather than by joining commas, because a cell
    containing a comma, a quote or a newline is common in a caption row and
    hand rolled joining corrupts exactly those rows.
    """
    table = element.get("table") or {}
    grid = table.get("grid") or []
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in grid:
        writer.writerow([(cell or "") for cell in row])
    return buffer.getvalue()


def table_filename(paper: dict[str, Any], element: dict[str, Any]) -> str:
    label = slugify(element.get("label") or f"table-page-{element.get('page', 0)}")
    return f"{slugify(paper.get('title') or 'paper')[:50]}-{label}.csv"


def figure_filename(paper: dict[str, Any], element: dict[str, Any]) -> str:
    label = slugify(element.get("label") or f"figure-page-{element.get('page', 0)}")
    return f"{slugify(paper.get('title') or 'paper')[:50]}-{label}.png"


_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def resolve_citations(text: str, citations: list[dict[str, Any]]) -> str:
    """Rewrite "[3]" as "[p. 7]" so the text still means something elsewhere.

    An excerpt number is an index into a list that exists only in the request
    that produced it. Exported without resolution the citations are noise; the
    page is the thing a reader can actually go and check.
    """
    by_number = {int(c["n"]): c for c in citations if c.get("n") is not None}

    def replace(match: re.Match[str]) -> str:
        parts: list[str] = []
        for raw in re.findall(r"\d+", match.group(1)):
            citation = by_number.get(int(raw))
            if citation is None:
                continue
            label = citation.get("label") or ""
            page = citation.get("page") or 0
            if label and page:
                parts.append(f"{label}, p. {page}")
            elif page:
                parts.append(f"p. {page}")
            elif label:
                parts.append(label)
        return f"[{'; '.join(parts)}]" if parts else ""

    return _CITATION.sub(replace, text)


def analysis_markdown(
    paper: dict[str, Any], analyses: list[dict[str, Any]]
) -> str:
    """Every stored analysis for a paper as one Markdown document."""
    lines: list[str] = [f"# {paper.get('title') or paper.get('filename') or 'Paper'}", ""]

    authors = paper.get("authors") or []
    if authors:
        lines.append(", ".join(authors))
    facts = []
    if paper.get("year"):
        facts.append(str(paper["year"]))
    if paper.get("venue"):
        facts.append(str(paper["venue"]))
    if paper.get("arxiv_id"):
        facts.append(f"arXiv:{paper['arxiv_id']}")
    if paper.get("doi"):
        facts.append(f"doi:{paper['doi']}")
    if facts:
        lines.append(" · ".join(facts))
    lines.append("")

    if paper.get("abstract"):
        lines += ["## Abstract", "", str(paper["abstract"]), ""]

    if paper.get("verdict"):
        lines += ["## Verdict", "", str(paper["verdict"]), ""]

    for analysis in analyses:
        content = str(analysis.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"## {str(analysis.get('lens') or 'Analysis').replace('_', ' ').title()}")
        lines.append("")
        lines.append(resolve_citations(content, list(analysis.get("citations") or [])))
        lines.append("")
        model = analysis.get("model") or analysis.get("provider")
        if model:
            lines.append(f"*Written by {model} from excerpts of this paper only.*")
            lines.append("")

    quality = paper.get("quality") or {}
    if quality.get("concerns"):
        lines += ["## Extraction notes", ""]
        for concern in quality["concerns"]:
            lines.append(f"- **{concern.get('severity', '')}**: {concern.get('message', '')}")
        lines.append("")

    lines.append("---")
    lines.append(
        "Extracted and analysed locally. Every claim above was written only "
        "from excerpts of this paper, with citations resolved to page numbers."
    )
    return "\n".join(lines)


def _bibtex_key(paper: dict[str, Any]) -> str:
    authors = paper.get("authors") or []
    surname = ""
    if authors:
        parts = str(authors[0]).replace(".", " ").split()
        surname = parts[-1] if parts else ""
    year = paper.get("year") or ""
    word = ""
    for token in re.findall(r"[A-Za-z]{4,}", str(paper.get("title") or "")):
        word = token.lower()
        break
    return re.sub(r"[^A-Za-z0-9]", "", f"{surname}{year}{word}") or "paper"


def _escape(value: str) -> str:
    # Braces protect capitalisation, which BibTeX otherwise lowercases in
    # titles, turning "BLEU" into "bleu".
    return str(value).replace("{", "").replace("}", "").strip()


def bibtex(paper: dict[str, Any]) -> str:
    """A BibTeX entry for the paper, from whatever metadata was recovered."""
    fields: list[tuple[str, str]] = []
    if paper.get("title"):
        fields.append(("title", "{" + _escape(paper["title"]) + "}"))
    if paper.get("authors"):
        fields.append(("author", _escape(" and ".join(paper["authors"]))))
    if paper.get("year"):
        fields.append(("year", str(paper["year"])))
    if paper.get("venue"):
        fields.append(("booktitle", _escape(paper["venue"])))
    if paper.get("doi"):
        fields.append(("doi", _escape(paper["doi"])))
    if paper.get("arxiv_id"):
        fields.append(("eprint", _escape(paper["arxiv_id"])))
        fields.append(("archivePrefix", "arXiv"))
    if paper.get("source_url"):
        fields.append(("url", _escape(paper["source_url"])))

    kind = "article" if paper.get("arxiv_id") or paper.get("doi") else "misc"
    body = ",\n".join(f"  {name} = {{{value}}}" if not value.startswith("{")
                      else f"  {name} = {value}" for name, value in fields)
    return f"@{kind}{{{_bibtex_key(paper)},\n{body}\n}}\n"


def library_markdown(papers: list[dict[str, Any]]) -> str:
    """The library as a reading list, with verdicts.

    This is what you would paste into a literature review's working notes:
    what you read, what you decided, and why.
    """
    lines = ["# Reading list", ""]
    by_state: dict[str, list[dict[str, Any]]] = {}
    for paper in papers:
        by_state.setdefault(str(paper.get("read_state") or "unread"), []).append(paper)

    for state in ("read", "reading", "unread", "rejected"):
        group = by_state.get(state) or []
        if not group:
            continue
        lines.append(f"## {state.title()} ({len(group)})")
        lines.append("")
        for paper in group:
            title = paper.get("title") or paper.get("filename") or "Untitled"
            authors = paper.get("authors") or []
            who = f" — {authors[0]} et al." if len(authors) > 2 else (
                f" — {', '.join(authors)}" if authors else ""
            )
            year = f" ({paper['year']})" if paper.get("year") else ""
            lines.append(f"- **{title}**{who}{year}")
            if paper.get("verdict"):
                lines.append(f"  - {paper['verdict']}")
        lines.append("")
    return "\n".join(lines)


def paper_bundle(paper_id: int) -> dict[str, str]:
    """Everything exportable for one paper, keyed by filename."""
    paper = repo.get_paper(paper_id)
    if paper is None:
        return {}
    analyses = [
        repo.get_analysis(paper_id, row["lens"]) or {}
        for row in repo.list_analyses(paper_id)
    ]
    stem = slugify(paper.get("title") or "paper")[:50]
    out = {
        f"{stem}.md": analysis_markdown(paper, [a for a in analyses if a]),
        f"{stem}.bib": bibtex(paper),
    }
    for element in repo.list_elements(paper_id, kinds=["table"]):
        if element.get("table"):
            out[table_filename(paper, element)] = table_csv(element)
    return out

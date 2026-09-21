"""Mapping a paper's headings onto the sections a reader asks about.

Papers are written to a small number of section conventions but name them
inconsistently: "Methods", "Methodology", "Materials and Methods", "Approach",
"Our Model", "3 Model Architecture". A question like "what did they actually
do" has to reach all of those, and a question about credibility has to reach
"Limitations", "Threats to Validity" and "Discussion" alike.

The mapping is deliberately lexical rather than learned. A classifier here
would need a model on the parse path, would be wrong in ways nobody could
debug, and would buy very little: the naming space is small and the cost of a
miss is only that an element falls back to UNKNOWN, which still indexes and
still retrieves. Order matters, because "Related Work" contains the word "work"
and "Experimental Setup" contains "experiment", so the more specific patterns
are tested first.
"""
from __future__ import annotations

import re

from ..util import normalise_text
from .base import Section

# Leading numbering a heading carries: "3", "3.2", "IV", "A.1", "Appendix B".
_NUMBER_PREFIX = re.compile(
    r"^\s*(?:(?:appendix|annex|part|chapter|section)\s+)?"
    r"(?:[ivxlcdm]+|[0-9]+|[a-z])"
    r"(?:[.)]\s*(?:[0-9]+|[a-z]))*[.)]?\s+",
    re.IGNORECASE,
)

# Tested in order. The first pattern that matches the stripped heading wins.
_PATTERNS: tuple[tuple[Section, re.Pattern[str]], ...] = (
    (Section.REFERENCES, re.compile(
        r"^(references?|bibliography|works cited|literature cited)\b")),
    (Section.ACKNOWLEDGEMENTS, re.compile(
        r"^(acknowledge?ments?|acknowledgment|funding|author contributions?|"
        r"competing interests?|conflicts? of interest|disclosure)\b")),
    (Section.ETHICS, re.compile(
        r"^(ethics?|ethical considerations?|broader impacts?|societal impacts?|"
        r"responsible (use|ai)|impact statement)\b")),
    # Tested before METHODS and DISCUSSION, both of which otherwise swallow it.
    (Section.LIMITATIONS, re.compile(
        r"^(limitations?|threats? to validity|shortcomings?|weaknesses|"
        r"failure (cases|modes|analysis)|when (it|this) fails|"
        r"caveats?|assumptions? and limitations?)\b")),
    (Section.RELATED_WORK, re.compile(
        r"^(related works?|prior works?|previous works?|related (literature|research)|"
        r"literature review|state of the art|comparison (to|with) (prior|existing))\b")),
    (Section.ABSTRACT, re.compile(r"^(abstract|summary|synopsis)\b")),
    (Section.INTRODUCTION, re.compile(r"^(introduction|motivation|overview)\b")),
    (Section.CONCLUSION, re.compile(
        r"^(conclusions?|concluding remarks|final remarks|"
        r"summary and (conclusions?|outlook)|future work|outlook)\b")),
    (Section.APPENDIX, re.compile(
        r"^(appendix|appendices|annexe?s?|supplement(ary|al)?"
        r"(\s+(material|information|results?))?)\b")),
    # "Data" before "Experiments": a "Data and Experiments" heading is more
    # usefully filed under data, which is what dataset questions look for.
    (Section.DATA, re.compile(
        r"^(datasets?|data|corpus|corpora|data (collection|preparation|sources?|set)|"
        r"materials|participants|subjects|sample|study (population|design)|"
        r"benchmarks?|training data)\b")),
    (Section.EXPERIMENTS, re.compile(
        r"^(experiments?|experimental (setup|design|settings?|protocol)|"
        r"evaluation( setup| protocol| methodology)?|setup|"
        r"implementation details?|hyper-?parameters?|"
        # "5 Training", "5.4 Regularization" and "Residual Dropout" are where
        # a paper puts the numbers a reproducibility question asks for, and all
        # three were landing in UNKNOWN.
        r"training( details| regime| setup| procedure| schedule)?|"
        r"regulari[sz]ation|dropout|optimi[sz](er|ation)|learning rate|"
        r"ablations?|ablation (study|studies|analysis))\b")),
    (Section.METHODS, re.compile(
        r"^(methods?|methodology|materials and methods|approach|our (approach|method|model)|"
        r"proposed (method|approach|model|framework|system)|model( architecture)?|"
        r"architecture|algorithm|framework|system design|"
        r"technical (approach|details)|formulation|problem (formulation|statement)|"
        r"preliminaries|notation)\b")),
    (Section.RESULTS, re.compile(
        r"^(results?|findings?|empirical results?|main results?|"
        r"performance|evaluation results?|quantitative (results?|analysis|evaluation)|"
        r"qualitative (results?|analysis|examples)|analysis)\b")),
    (Section.DISCUSSION, re.compile(
        # A heading that opens with "why" is arguing for a design choice,
        # wherever in the paper it sits. "Why Self-Attention" in the
        # Transformer paper is the canonical case, and it was landing in
        # UNKNOWN, which put the single most cited justification in that paper
        # out of reach of any section filter.
        r"^(discussions?|interpretation|implications?|why\b|"
        r"error analysis|case stud(y|ies)|trade-?offs?)\b")),
    (Section.BACKGROUND, re.compile(
        r"^(background|preliminary|theory|theoretical (background|framework)|"
        r"definitions?|terminology|context)\b")),
)


def strip_numbering(heading: str) -> str:
    """Remove a heading's outline number, leaving the words.

    Applied at most once. A heading that is only a number, which a badly
    parsed PDF produces, would otherwise be stripped to nothing.
    """
    text = (heading or "").strip()
    stripped = _NUMBER_PREFIX.sub("", text, count=1).strip()
    return stripped or text


def section_of(heading: str) -> Section:
    """Classify one heading. UNKNOWN when nothing matches, which is fine."""
    text = normalise_text(strip_numbering(heading))
    if not text:
        return Section.UNKNOWN
    # A heading is sometimes a whole sentence because the parser caught a run
    # of bold body text. Only the opening words carry the section name.
    head = text[:80]
    for section, pattern in _PATTERNS:
        if pattern.search(head):
            return section
    return Section.UNKNOWN


def section_for_path(path: list[str]) -> Section:
    """The section an element belongs to, given its whole heading trail.

    The innermost heading that classifies at all wins, so "4 Experiments >
    4.3 Limitations of our setup" is filed as limitations rather than
    experiments. An element under no classifiable heading inherits from the
    nearest ancestor that does classify, and only falls back to UNKNOWN when
    the whole trail is unrecognised.
    """
    for heading in reversed(path or []):
        found = section_of(heading)
        if found is not Section.UNKNOWN:
            return found
    return Section.UNKNOWN


# What each section is good for, shown in the UI and used by the analysis
# lenses to decide where to retrieve from. A lens asking about credibility
# reads limitations, methods and data; one asking what was found reads results.
SECTION_LABELS: dict[Section, str] = {
    Section.FRONT: "Front matter",
    Section.ABSTRACT: "Abstract",
    Section.INTRODUCTION: "Introduction",
    Section.BACKGROUND: "Background",
    Section.RELATED_WORK: "Related work",
    Section.METHODS: "Methods",
    Section.DATA: "Data and materials",
    Section.EXPERIMENTS: "Experimental setup",
    Section.RESULTS: "Results",
    Section.DISCUSSION: "Discussion",
    Section.LIMITATIONS: "Limitations",
    Section.CONCLUSION: "Conclusion",
    Section.ETHICS: "Ethics and impact",
    Section.ACKNOWLEDGEMENTS: "Acknowledgements",
    Section.REFERENCES: "References",
    Section.APPENDIX: "Appendix",
    Section.UNKNOWN: "Unclassified",
}

# Reading order for the outline view, so sections appear as a paper would
# present them rather than in whatever order the classifier saw them.
SECTION_ORDER: tuple[Section, ...] = (
    Section.FRONT,
    Section.ABSTRACT,
    Section.INTRODUCTION,
    Section.BACKGROUND,
    Section.RELATED_WORK,
    Section.METHODS,
    Section.DATA,
    Section.EXPERIMENTS,
    Section.RESULTS,
    Section.DISCUSSION,
    Section.LIMITATIONS,
    Section.CONCLUSION,
    Section.ETHICS,
    Section.ACKNOWLEDGEMENTS,
    Section.REFERENCES,
    Section.APPENDIX,
    Section.UNKNOWN,
)


def order_index(section: Section) -> int:
    try:
        return SECTION_ORDER.index(section)
    except ValueError:
        return len(SECTION_ORDER)

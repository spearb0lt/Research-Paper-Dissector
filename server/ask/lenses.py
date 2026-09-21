"""Named ways of reading a paper.

A lens is a retrieval recipe plus a prompt. Both halves matter: asking for
limitations and retrieving from the results section produces a confident answer
about the wrong part of the paper, so each lens says which sections it wants
and what queries to retrieve with before it says what to write.

The set is chosen around one observation: what separates a paper you can trust
from one you cannot is almost never in the abstract. It is in the methodology,
the dataset, the baselines, the statistics and the limitations, and those are
exactly the sections a reader skips. Several of these lenses exist to make that
skipping expensive rather than free.

The comparison lenses at the bottom are what makes a library worth more than
the sum of its papers. They are kept separate because comparing a paper with
itself is not a thing to offer.

Adding a lens is a dict entry. Nothing else in the application needs changing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..parse.base import Section


@dataclass(frozen=True)
class Lens:
    id: str
    label: str
    description: str
    # What to retrieve with. Several short queries beat one long one, because
    # each is fused separately and a paper rarely phrases a concept one way.
    queries: tuple[str, ...]
    # Sections to prefer. Empty means the whole paper. This is a preference
    # rather than a filter: a paper with no limitations section must still get
    # an answer about its limitations, assembled from wherever they are.
    sections: tuple[str, ...] = ()
    instruction: str = ""
    # How much evidence to put in front of the model. For a comparison lens
    # this is per paper, not in total.
    top_k: int = 12
    # Whether retrieved figures should be sent as images to a vision model.
    wants_images: bool = False
    group: str = "read"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "group": self.group,
            "wants_images": self.wants_images,
        }


_S = Section

LENSES: tuple[Lens, ...] = (
    Lens(
        id="tldr",
        label="TL;DR",
        description="Three sentences: the problem, what they did, what they found.",
        group="read",
        queries=(
            "main contribution of this paper",
            "what problem does this work solve",
            "headline result",
        ),
        sections=(_S.ABSTRACT.value, _S.INTRODUCTION.value, _S.CONCLUSION.value,
                  _S.RESULTS.value),
        top_k=10,
        instruction=(
            "Write exactly three sentences.\n"
            "1. The problem, stated concretely enough that someone could tell "
            "whether it applies to them.\n"
            "2. What the authors actually did, naming the method.\n"
            "3. The headline result, with the number and the benchmark.\n"
            "No preamble. Do not begin with the paper's title."
        ),
    ),
    Lens(
        id="structured_abstract",
        label="Structured summary",
        description="Background, objective, method, results, conclusion, as separate fields.",
        group="read",
        queries=(
            "background and motivation", "objective of the study",
            "method used", "main results", "conclusion",
        ),
        top_k=16,
        instruction=(
            "Write a structured summary under these exact headings, one short "
            "paragraph each: Background, Objective, Method, Results, "
            "Conclusion. If the paper does not supply one of them, write "
            "'Not stated' under that heading rather than inferring it."
        ),
    ),
    Lens(
        id="methodology",
        label="How it works",
        description="The method, dissected step by step, in the order it runs.",
        group="read",
        queries=(
            "proposed method architecture", "how the model works",
            "algorithm steps", "training procedure", "implementation details",
        ),
        sections=(_S.METHODS.value, _S.EXPERIMENTS.value, _S.BACKGROUND.value),
        top_k=16,
        wants_images=True,
        instruction=(
            "Explain how the method works, in the order it runs, as numbered "
            "steps. For each step say what goes in, what happens and what "
            "comes out. Name every component the paper names. Where a figure "
            "shows the step, cite it. Where the paper is vague about a step, "
            "say so plainly rather than filling the gap."
        ),
    ),
    Lens(
        id="data",
        label="Data and setup",
        description="Datasets, sizes, splits, preprocessing, hardware and hyperparameters.",
        group="rigour",
        queries=(
            "dataset used", "training data size and splits", "preprocessing",
            "hyperparameters learning rate batch size", "hardware and training time",
            "evaluation metrics",
        ),
        sections=(_S.DATA.value, _S.EXPERIMENTS.value, _S.METHODS.value),
        top_k=16,
        instruction=(
            "Extract the experimental setup as a list with these fields, one "
            "line each, copying every number exactly as the paper gives it:\n"
            "Datasets, Size, Splits, Preprocessing, Model size, Key "
            "hyperparameters, Hardware, Training time, Metrics, Baselines.\n"
            "Write 'Not reported' for any field the paper does not state. Do "
            "not infer a value from a related one."
        ),
    ),
    Lens(
        id="results",
        label="What they found",
        description="Every reported result, with the number, the baseline and the benchmark.",
        group="rigour",
        queries=(
            "main results table", "performance compared to baselines",
            "improvement over previous work", "ablation results",
        ),
        sections=(_S.RESULTS.value, _S.EXPERIMENTS.value, _S.DISCUSSION.value),
        top_k=16,
        instruction=(
            "List every quantitative result the paper reports, as a markdown "
            "table with columns: Task or benchmark, Metric, This work, Best "
            "baseline, Difference. Copy numbers exactly. Where the paper gives "
            "no baseline for a row, leave that cell empty rather than "
            "inventing a comparison. Below the table, note in one or two "
            "sentences which comparisons are like for like and which are not."
        ),
    ),
    Lens(
        id="limitations",
        label="Limitations",
        description="What the authors admit, and what they quietly do not.",
        group="rigour",
        queries=(
            "limitations of this work", "threats to validity",
            "failure cases", "future work", "assumptions made",
            "what this method cannot do",
        ),
        sections=(_S.LIMITATIONS.value, _S.DISCUSSION.value, _S.CONCLUSION.value,
                  _S.ETHICS.value, _S.METHODS.value),
        top_k=16,
        instruction=(
            "Two sections.\n\n"
            "**Stated by the authors**: limitations the paper explicitly "
            "acknowledges, each cited.\n\n"
            "**Visible in the setup**: limitations that follow from what the "
            "excerpts show rather than from what the authors say, for example "
            "a single dataset, a single language, no variance reported, no "
            "comparison against a strong baseline, or an evaluation on the "
            "data used for tuning. Each one must point at the specific "
            "excerpt it follows from. If an excerpt does not support a "
            "concern, do not raise it. Write 'Nothing further visible in "
            "these excerpts' rather than padding this section."
        ),
    ),
    Lens(
        id="credibility",
        label="Claims vs evidence",
        description="Each headline claim, matched against the evidence actually offered.",
        group="rigour",
        queries=(
            "main claims of the paper", "we show that", "our results demonstrate",
            "statistical significance", "ablation study", "baseline comparison",
            "sample size and variance",
        ),
        top_k=18,
        instruction=(
            "Produce a markdown table with columns: Claim, Evidence offered, "
            "How strong, Why.\n"
            "Take each substantive claim the paper makes. In 'Evidence "
            "offered' cite the excerpt that supports it. In 'How strong' write "
            "one of Strong, Moderate, Weak or Unsupported, judged only on what "
            "the excerpts contain: whether the comparison is controlled, "
            "whether variance or significance is reported, whether the "
            "evaluation is independent of the tuning, and whether the claim's "
            "scope exceeds what was tested. A claim you cannot find evidence "
            "for in the excerpts is Unsupported, and say that plainly."
        ),
    ),
    Lens(
        id="reproducibility",
        label="Could you reproduce it?",
        description="A checklist of what someone would need, and what is missing.",
        group="rigour",
        queries=(
            "code availability", "data availability", "hyperparameters",
            "random seeds", "hardware used", "implementation details",
            "experimental protocol",
        ),
        top_k=16,
        instruction=(
            "Go through this checklist and mark each item Yes, Partial or No, "
            "with one line of justification and a citation where the answer is "
            "Yes or Partial: code released, data released or public, exact "
            "model and version, all hyperparameters, random seeds, number of "
            "runs, variance reported, hardware, compute budget, evaluation "
            "code, preprocessing steps. Finish with one sentence on the single "
            "biggest obstacle to reproducing this work."
        ),
    ),
    Lens(
        id="related",
        label="Where it sits",
        description="What it builds on, what it competes with, what it claims to beat.",
        group="context",
        queries=(
            "related work", "prior approaches", "compared to previous methods",
            "builds on", "differs from existing work",
        ),
        sections=(_S.RELATED_WORK.value, _S.INTRODUCTION.value, _S.BACKGROUND.value),
        top_k=14,
        instruction=(
            "Three short sections: **Builds on**, **Competes with**, "
            "**Claims to beat**. Under each, list the specific methods or "
            "papers named in the excerpts, one per line, each with a few words "
            "on the relationship. Name only what the excerpts name."
        ),
    ),
    Lens(
        id="glossary",
        label="Jargon",
        description="Every term of art the paper assumes you know, defined.",
        group="context",
        queries=(
            "definition of terms", "notation used", "we define",
            "background concepts", "preliminaries",
        ),
        top_k=14,
        instruction=(
            "List the terms, symbols and abbreviations a reader would need in "
            "order to follow this paper, as 'term: definition', one per line, "
            "ordered by how early the paper needs them. Define each in one "
            "sentence of plain language. Where the paper defines the term, "
            "cite it. Where it does not and the meaning is standard in the "
            "field, define it and mark it '(standard usage, not defined in "
            "the paper)'."
        ),
    ),
    Lens(
        id="explain",
        label="Explain it simply",
        description="The whole paper for someone one field over, no jargon.",
        group="read",
        queries=(
            "main idea", "intuition behind the method", "why this works",
            "what problem this solves", "main result",
        ),
        top_k=14,
        wants_images=True,
        instruction=(
            "Explain this paper to a capable researcher from a different "
            "field. Four short paragraphs: what problem exists and why it "
            "matters, the key idea in plain language with an analogy if one "
            "genuinely fits, what they did, and what it means. Define every "
            "term of art the first time you use it. Do not simplify to the "
            "point of being wrong: if the real idea is technical, say the "
            "technical thing and then explain it."
        ),
    ),
    Lens(
        id="figures",
        label="Read the figures",
        description="What each figure and table actually shows.",
        group="context",
        queries=(
            "figure shows", "table reports", "results plotted",
            "architecture diagram", "comparison chart",
        ),
        top_k=16,
        wants_images=True,
        instruction=(
            "Go through the figures and tables in the excerpts in order. For "
            "each, write its label, then one line on what it plots or "
            "tabulates, then one line on what it is meant to convince you of, "
            "then one line on whether it does. Where a table's structure was "
            "flagged as inferred, say that its numbers should be checked "
            "against the image before being relied on."
        ),
    ),
)


# Lenses that only make sense across several papers.
#
# These retrieve per paper rather than across the pool, because a pooled
# retrieval lets the most verbose paper take every slot, and a comparison of
# one paper against silence is worse than no comparison.
COMPARISON_LENSES: tuple[Lens, ...] = (
    Lens(
        id="compare",
        label="Compare these papers",
        description="A side by side matrix: problem, data, method, result, limitation.",
        group="compare",
        queries=(
            "main contribution", "dataset used", "method proposed",
            "main quantitative result", "baselines compared against",
            "limitations of this work",
        ),
        top_k=8,
        instruction=(
            "Produce one markdown table comparing the papers, one row per "
            "paper, with columns: Paper, Problem, Data, Method, Headline "
            "result, Compared against, Main limitation.\n"
            "Keep every cell to a short phrase, not a sentence. Copy numbers "
            "exactly. Cite the excerpt each cell came from. Where the excerpts "
            "for a paper do not cover a column, write 'Not in excerpts' rather "
            "than filling it from another paper or from memory.\n\n"
            "Below the table write two short sections.\n"
            "**Comparable**: what can honestly be compared across these "
            "papers, and on what common ground.\n"
            "**Not comparable**: where a direct comparison would mislead, for "
            "example different datasets, metrics, splits or hardware. Name the "
            "specific papers and columns."
        ),
    ),
    Lens(
        id="compare_results",
        label="Compare the numbers",
        description="Every reported result from every paper, in one table.",
        group="compare",
        queries=(
            "main results table", "accuracy score", "performance compared to baselines",
            "benchmark results", "evaluation metrics reported",
        ),
        sections=(_S.RESULTS.value, _S.EXPERIMENTS.value),
        top_k=10,
        instruction=(
            "Produce one markdown table of every quantitative result across "
            "all the papers, with columns: Paper, Task or benchmark, Metric, "
            "Value, Baseline value. One row per result, not per paper. Copy "
            "numbers exactly and cite every row.\n\n"
            "Then, in at most four sentences, say which of these numbers are "
            "actually comparable with each other and which are not, and why."
        ),
    ),
    Lens(
        id="compare_methods",
        label="Compare the approaches",
        description="How each paper's method differs, and what they share.",
        group="compare",
        queries=(
            "proposed method", "model architecture", "how the approach works",
            "key idea", "differs from prior work",
        ),
        sections=(_S.METHODS.value, _S.BACKGROUND.value),
        top_k=10,
        instruction=(
            "Write two sections.\n\n"
            "**Shared**: what these papers have in common, as bullets, each "
            "citing the papers it applies to.\n\n"
            "**Different**: a markdown table with one row per paper and "
            "columns: Paper, Core idea, What is new, Key assumption. Cite "
            "every cell. Do not invent a difference the excerpts do not show."
        ),
    ),
)

BY_ID: dict[str, Lens] = {lens.id: lens for lens in (*LENSES, *COMPARISON_LENSES)}

GROUPS: dict[str, str] = {
    "read": "Understand it",
    "rigour": "Interrogate it",
    "context": "Place it",
    "compare": "Compare them",
}


def get(lens_id: str) -> Lens | None:
    return BY_ID.get((lens_id or "").strip().lower())


def is_comparison(lens_id: str) -> bool:
    return any(lens.id == (lens_id or "").strip().lower() for lens in COMPARISON_LENSES)


def catalogue(*, comparison: bool = False) -> list[dict[str, Any]]:
    """The lenses on offer. Comparison lenses only when several papers are in play."""
    pool = COMPARISON_LENSES if comparison else LENSES
    return [
        {
            "id": group_id,
            "label": label,
            "lenses": [lens.as_dict() for lens in pool if lens.group == group_id],
        }
        for group_id, label in GROUPS.items()
        if any(lens.group == group_id for lens in pool)
    ]

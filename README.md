# CV — Nguyễn Phương Anh Tú

Source of truth for my CV — and a reusable, profile-driven pipeline that builds
a defensible developer CV for **anyone**. Four things live in this repo:

1. **`profile.yaml`** — the single input file: identity + research sources +
   experience/education/skills. The crawler reads its `research_sources`; the CV
   is authored from the rest. Copy **`profile.example.yaml`** to start a new one.
2. **`cv_data.yaml`** — [rendercv](https://docs.rendercv.com) source, written from
   `profile.yaml` + verified numbers. Render this to PDF.
3. **`scripts/research.py`** — refreshes verifiable metrics the CV cites
   (publication downloads, citation counts and citing-paper list, GitHub
   stars/forks). Output is committed at `data/research.{json,md}` so the CV
   numbers stay defensible against a live recruiter check.
4. **`agents/skills/defensible-cv/SKILL.md`** — the agent playbook. Any AI coding
   agent that reads `SKILL.md` files auto-discovers it; its `description` fires
   when you ask the agent to build/edit the CV, refresh metrics, or render — and
   it **stops to ask for `profile.yaml`** if one isn't present.

## Quickstart

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# Refresh metrics (data/research.{json,md}). Friendly to public APIs:
# caches each URL for 24h under data/.cache and backs off on 429.
.venv/bin/python scripts/research.py            # full refresh + Scholar (optional dep)
.venv/bin/python scripts/research.py --no-scholar   # skip Scholar (CI default)
.venv/bin/python scripts/research.py --refresh      # bypass cache
.venv/bin/python scripts/research.py --print        # also print summary to stdout

# Render the CV to PDF — canonical path, no venv needed
uv run --with "rendercv[full]>=2.8" rendercv render cv_data.yaml

# …or, inside the venv created above:
.venv/bin/rendercv render cv_data.yaml
```

## Generate a CV for someone else

The whole repo is profile-driven, so reusing it for another person is three steps:

```bash
cp profile.example.yaml profile.yaml   # then fill it in (identity + research_sources + narrative)
uv run --with requests --with pyyaml --with pypdf \
  python scripts/research.py --no-scholar   # verifies their GitHub + publications
# then ask an agent (see SKILL.md) to author cv_data.yaml and render the PDF
```

`scripts/research.py` reads **only** `profile.yaml`'s `research_sources`
(GitHub username + publication DOIs/OJS URLs + per-paper match signatures). If
`profile.yaml` is missing it stops with a clear message instead of inventing
data — copy the template and fill it first. The committed `profile.yaml` /
`cv_data.yaml` are a complete worked example.

## Agent skill

This repo ships an agent skill at **`agents/skills/defensible-cv/SKILL.md`** —
the exact file name AI coding agents look for. You don't "run" it: an agent
that indexes `SKILL.md` files loads it automatically, and its `description`
makes it activate when you ask things like *"update the CV"*, *"refresh the
citation numbers"*, or *"re-render the PDF"*.

What it enforces so the output stays professional and defensible:

- every number in `cv_data.yaml` must trace back to `data/research.json` — run
  `scripts/research.py` first; never invent figures;
- render with `uv run --with "rendercv[full]>=2.8" rendercv render cv_data.yaml`
  and visually confirm the result stays **2 pages**;
- known rendercv/typst traps (`;` dropped right after `**bold**`, the `tel:`
  phone prefix, `|-` vs `>-` scalars) plus the spacing knobs that hold the
  layout to two pages.

**To use it:** open this repo in an agent that supports `SKILL.md` skills and
ask in plain language. **To read it yourself:** open
`agents/skills/defensible-cv/SKILL.md`.

## What `research.py` checks

| Source | What it returns |
| --- | --- |
| OJS (jte.edu.vn) | Per-article download total, parsed from the inline `pkpUsageStats` payload. |
| Semantic Scholar Graph API | Paper metadata + paginated citing-paper list with venue, year, authors, DOI. |
| OpenCitations COCI/Meta | Independent citation count and citing DOIs (cross-checks Semantic Scholar). |
| Crossref Event Data | Twitter / Wikipedia / news mentions (best-effort). |
| Google Scholar (`scholarly`) | Optional, soft-fails on CAPTCHA. Run with `--no-scholar` to skip. |
| GitHub Public API | Per-repo stars/forks, languages, totals across non-fork repos. |
| **Citing-paper PDFs** (Unpaywall → arXiv → `pypdf`) | **Per open-access citing paper: downloads the PDF, parses per-page text, locates the reference label (`[19]`, `[43]`, `(Nguyen & Hoang, 2024)`, …) that points back to our work, and extracts the verbatim sentence + page number where the citing author actually invokes it.** |

Citing papers are auto-tagged `[IEEE] / [ACM] / [Elsevier] / [Springer] / [Nature] / [MDPI]`
by DOI prefix so a recruiter glancing at `data/research.md` can see at once that
the work is being cited at top venues.

### Concrete citation contexts

For every citing paper whose PDF is open-access, `data/research.md` lists:

- the reference label our work has in that paper (e.g. `[19]`, `[43]`),
- the page of their reference list,
- the actual sentence(s) in the body where the citing author *uses* our work, with body page numbers.

Example produced by the current run for `10.54644/jte.2024.1514`:

> **[ACM]** *Integrating Expert Knowledge With Automated Knowledge Component Extraction for Student Modeling* — ACM UMAP 2025
> - Our paper is reference **[19]** (their p.6).
> - p.2 — _"…ASTs have been widely used in automated code analysis efforts **[19]**. For example, Rivers used ASTs to identify each syntax structure in a student's submission…"_

Paywalled citing papers (most IEEE, parts of ACM, MDPI behind Akamai) are kept in the report with status `not_oa` or `fetch_failed` and clearly flagged so nothing looks fabricated.

## Files

```
profile.yaml                 INPUT: identity + research sources + narrative
profile.example.yaml         documented template — copy to profile.yaml
cv_data.yaml                 rendercv source (authored from profile + research)
scripts/research.py          CLI crawler, single file, urllib-only
data/research.json           machine-readable snapshot (regenerated)
data/research.md             recruiter-friendly summary (regenerated)
data/.cache/                 SHA1-keyed HTTP cache, 24h TTL
data/.cache/pdfs/            binary PDF cache for citing-paper full-text
requirements.txt             pinned deps
agents/skills/defensible-cv/SKILL.md   agent playbook (auto-loaded by AI agents)
```

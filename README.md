<p align="center">
  <img src="assets/cv-preview.png" alt="Defensible CV — preview" width="100%">
</p>

<h1 align="center">defensible-cv</h1>

<p align="center">
  <b>English</b> · <a href="README.vi.md">Tiếng Việt</a>
</p>

<p align="center">
  A profile-driven pipeline that turns one input file into a recruiter-ready,
  2-page developer CV PDF — where <b>every number is verified live</b>
  (publication downloads, citations, GitHub stars/forks) and committed as proof.
</p>

---

## What this is

You fill in **`profile.yaml`** (identity + research sources + experience). A
Python crawler verifies the metrics against live public APIs and writes
`data/research.{json,md}`. Then the CV is authored into `cv_data.yaml`
([rendercv](https://docs.rendercv.com)) and rendered to a clean 2-page PDF.

```
profile.yaml  ──►  scripts/research.py  ──►  data/research.json + research.md
                                                      │
                                                      ▼
                                  cv_data.yaml  ──►  rendercv  ──►  PDF (2 pages)
```

Sample output: **[`assets/Nguyen_Phuong_Anh_Tu_CV.pdf`](assets/Nguyen_Phuong_Anh_Tu_CV.pdf)**.

## Use it with an AI agent (recommended)

This repo is built around an **agent skill**. Open it in any AI coding agent
that reads `AGENTS.md` / `SKILL.md` files (Claude, Cursor, …). The agent reads
[`AGENTS.md`](AGENTS.md) → [`agents/skills/defensible-cv/SKILL.md`](agents/skills/defensible-cv/SKILL.md),
and **stops to ask for `profile.yaml` if it's missing** — it never invents your data.

Then just ask, in plain language:

**Build a CV for a new person** (no profile yet):

> I want a developer CV. Here's my info — name: …, location: …, email: …,
> GitHub: …, publications (DOIs): …, experience: …, education: …, skills: ….
> Follow `AGENTS.md` + the defensible-cv skill: write `profile.yaml`, refresh the
> metrics, author `cv_data.yaml`, and render the PDF.

**Rebuild from an existing `profile.yaml`:**

> Build the CV: follow `AGENTS.md` + the defensible-cv skill. Refresh metrics
> from `profile.yaml`, then render the PDF.

**Refresh the numbers only:**

> Refresh the CV metrics and re-render the PDF.

## Do it manually (no agent)

```bash
# 1. Create your profile from the template, then fill it in
cp profile.example.yaml profile.yaml

# 2. Verify metrics (reads profile.yaml's research_sources; --no-scholar = CI-safe)
uv run --with requests --with pyyaml --with pypdf \
  python scripts/research.py --no-scholar      # --refresh bypasses 24h cache

# 3. Render the CV to PDF (no venv needed)
uv run --with "rendercv[full]>=2.8" rendercv render cv_data.yaml
```

`scripts/research.py` reads **only** `profile.yaml`'s `research_sources`. If
`profile.yaml` is missing it stops with a clear message instead of inventing
data. The committed `profile.yaml` / `cv_data.yaml` are a complete worked example.

## How it stays defensible

Every figure on the CV is verified live, then committed to `data/research.json`
so it survives a recruiter's spot-check:

| Source | What it returns |
| --- | --- |
| OJS (e.g. jte.edu.vn) | Per-article download total from the inline `pkpUsageStats` payload. |
| Semantic Scholar Graph API | Paper metadata + paginated citing-paper list (venue, year, authors, DOI). |
| OpenCitations COCI/Meta | Independent citation count + citing DOIs (cross-checks Semantic Scholar). |
| Crossref Event Data | Twitter / Wikipedia / news mentions (best-effort). |
| Google Scholar (`scholarly`) | Optional, soft-fails on CAPTCHA. `--no-scholar` skips it. |
| GitHub Public API | Per-repo stars/forks, languages, totals across non-fork repos. |
| **Citing-paper PDFs** (Unpaywall → arXiv → `pypdf`) | **Downloads each open-access citing paper, finds the reference label that points back to the work, and extracts the verbatim sentence + page where the author invokes it.** |

### Concrete citation contexts

For every open-access citing paper, `data/research.md` records the reference
label, the reference-list page, and the in-body sentence(s) that cite the work.
Example from the committed run for `10.54644/jte.2024.1514`:

> **[ACM]** *Integrating Expert Knowledge With Automated Knowledge Component
> Extraction for Student Modeling* — ACM UMAP 2025
> - Our paper is reference **[19]** (their p.6).
> - p.2 — *"…ASTs have been widely used in automated code analysis efforts **[19]**.
>   For example, Rivers used ASTs to identify each syntax structure in a student's
>   submission…"*

Paywalled papers (most IEEE, parts of ACM, MDPI behind Akamai) are kept with
status `not_oa` / `fetch_failed` and clearly flagged — nothing is fabricated.

## Files

```
profile.yaml                 INPUT: identity + research sources + narrative
profile.example.yaml         documented template — copy to profile.yaml
cv_data.yaml                 rendercv source (authored from profile + research)
scripts/research.py          CLI crawler, single file, urllib + pypdf
data/research.json           machine-readable verified snapshot (regenerated)
data/research.md             recruiter-friendly summary (regenerated)
AGENTS.md                    entry point for AI agents
agents/skills/defensible-cv/SKILL.md   the agent playbook
assets/cv-preview.png        the preview banner above
assets/Nguyen_Phuong_Anh_Tu_CV.pdf     sample rendered CV
requirements.txt             pinned deps
```

## License

[MIT](LICENSE).

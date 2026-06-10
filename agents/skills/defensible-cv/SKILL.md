---
name: defensible-cv
description: Turn a person's profile.yaml into a recruiter-ready, 2-page developer CV PDF where every number is verified live. Use whenever the user asks to generate a CV, build a resume for someone, update cv_data.yaml, refresh citation/download/star counts, or re-render the PDF. The pipeline is profile.yaml -> scripts/research.py -> data/research.json -> cv_data.yaml -> rendercv PDF. If profile.yaml is missing, STOP and ask the user for it; never invent identity, numbers, venues, or citing-paper contexts.
---

# defensible-cv

Generate a defensible developer CV for **any person** from a single input file
(`profile.yaml`). Every load-bearing number on the final PDF — publication
downloads, citation counts, citing-paper page references, GitHub stars/forks —
is verified live by `scripts/research.py` and mirrored in `data/research.json`.

## The pipeline (read this first)

```
profile.yaml                      ← the ONE file the user provides (who + sources)
   │  scripts/research.py --no-scholar   (reads research_sources only)
   ▼
data/research.json + research.md  ← verified live metrics (regenerated)
   │  you, the agent, author from profile narrative + verified numbers
   ▼
cv_data.yaml                      ← rendercv source (English, 2 pages)
   │  uv run --with "rendercv[full]>=2.8" rendercv render cv_data.yaml
   ▼
rendercv_output/…CV.pdf           ← the deliverable
```

## STOP gate — no profile, no CV

**Before anything else, check for `profile.yaml` at the repo root.**

- **If `profile.yaml` does NOT exist:** STOP. Do **not** fabricate a person, do
  **not** reuse the committed example (`profile.yaml`/`cv_data.yaml` in this repo
  belong to one specific person), and do **not** invent numbers. Tell the user
  you need their profile and offer two ways to provide it:
  1. `cp profile.example.yaml profile.yaml` and fill it in, or
  2. paste the facts in chat and you will write `profile.yaml` for them.

  List the **required** fields so they know the minimum:
  - `identity.name`, `identity.email` (and ideally `phone`, `location`, `headline`)
  - `research_sources.github_user` **and/or** at least one
    `research_sources.publications[].doi`
  - their `experience` / `education` / `skills` narrative (the crawler can't
    invent job history).

  Only proceed once `profile.yaml` exists.

- **If `profile.yaml` exists but `research_sources` has no `github_user` and no
  `publications`:** `scripts/research.py` exits 2 by design. Ask the user for at
  least a GitHub username or one publication DOI before continuing.

Never substitute missing facts with placeholders, guesses, or another person's
data. A missing profile is a question for the user, not a gap to paper over.

## Inputs & outputs

| Path | Role |
| --- | --- |
| `profile.yaml` | **Input.** Single source of identity + research sources + narrative. The user owns this. |
| `profile.example.yaml` | Documented template. Copy → `profile.yaml`. |
| `scripts/research.py` | urllib + pypdf crawler. Reads **only** `profile.yaml`'s `research_sources`. |
| `data/research.json` | **Generated.** Machine-readable verified metrics. Every CV number must trace here. |
| `data/research.md` | **Generated.** Recruiter-readable metrics summary with citation excerpts. |
| `cv_data.yaml` | **You author** this rendercv source from profile narrative + research.json numbers. |
| `data/.cache/` | 24h HTTP cache; `data/.cache/pdfs/` holds citing-paper PDFs. Gitignored. |
| `rendercv_output/` | Render artefacts (PDF/PNG/MD/HTML/Typst). Gitignored. |
| `requirements.txt` | `rendercv[full]>=2.8`, `PyYAML>=6.0`, `pypdf>=4.0`. `scholarly` optional/commented. |

## End-to-end workflow

1. **STOP gate** above. Confirm `profile.yaml` exists and has research sources.
2. **Refresh metrics:**
   ```bash
   uv run --with requests --with pyyaml --with pypdf \
     python scripts/research.py --no-scholar
   # custom profile path: add --profile path/to/profile.yaml
   # bypass 24h cache:    add --refresh
   # print summary:       add --print
   ```
3. **Author / update `cv_data.yaml`.** Take narrative (experience, education,
   projects, skills, identity) from `profile.yaml`; take every **number** from
   `data/research.json`. Map profile sections → rendercv entry types (see
   cheat-sheet). Surface the strongest verified metrics (top repos by stars,
   highest-download paper, citing-paper reference labels + pages from
   `data/research.md`).
4. **Render & visually verify:**
   ```bash
   rm -rf rendercv_output
   uv run --with "rendercv[full]>=2.8" rendercv render cv_data.yaml
   ```
   Open both PNGs. Confirm: 2 pages (no orphan page 3), no `;` dropped after
   `**bold**`, phone has no `tel:`, sections in order.
5. **ATS sanity check:**
   ```bash
   pdftotext rendercv_output/*CV.pdf - | head -80
   ```
   Name, email, phone, dates, section headings present and in reading order.

## Mandatory rules

1. **No profile → STOP and ask.** (See STOP gate.) Never invent identity or facts.
2. **No invented numbers.** Every figure in `cv_data.yaml` must be traceable to
   `data/research.json`. If it isn't there, run the crawler; never guess.
3. **No placeholder text in shipped copy.** `(live count in …)`, `sources tracked
   in …`, `TODO` are forbidden in `cv_data.yaml`. Inline the number or drop the claim.
4. **Re-render + visually check after every content change.** Done means the PDF
   renders, is 2 pages, and the PNGs look right.
5. **2-page maximum** (engineeringresumes theme). Overflow → tighten copy, then
   lower the design knobs below. Never drop a section.
6. **NEVER put `;` immediately after `**bold**`** — typst drops it. Use ` — `, `,`, or ` · `.
7. **No `tel:` prefix on `cv.phone`.** Use `+CC-NNN-NNN-NNNN`.
8. **Folded scalars (`>-`) for multi-line text**, never literal (`|-`).
9. **Render via `uv`**, not the project venv (venv is fine for the crawler).
10. **Crawler runs `--no-scholar` by default.** `scholarly` is optional/CAPTCHA-prone.

## profile.yaml structure

```yaml
identity:            # copied to cv_data.yaml verbatim; never invented
  name: …
  headline: "Role · Focus"
  location: City, Country
  email: …
  phone: "+CC-NNN-NNN-NNNN"
  website: https://…
  social_networks: [{network: GitHub, username: …}, …]

research_sources:    # THE ONLY SECTION THE CRAWLER READS
  github_user: handle           # or null/omit
  publications:                 # or omit
    - key: slug
      title: …
      doi: 10.x/y
      ojs_url: https://…        # optional (download counts)
      journal: …
      year: 2024
      lead_author: true
      authors_display: "Doe, J.; …"
      signatures: [doi, "Title Fragment", "Author Name"]  # for PDF citation matching

experience: [...]    # narrative; agent tightens wording, never fabricates
education:  [...]
projects:   [...]
skills:     [...]
```

## Schema cheat-sheet (rendercv v2.8)

- `experience_entry`: `company`, `position`, `start_date`, `end_date` (`YYYY-MM`/`present`), `location`, `summary?`, `highlights[]`
- `publication_entry`: `title`, `authors[]`, `doi`, `journal`, `date` (year), `summary?`
- `normal_entry` (Projects): `name` (may hold a markdown link), `date`, `summary?`, `highlights[]`
- `education_entry`: `institution`, `area`, `degree`, `start_date`, `end_date`, `location`, `summary?`, `highlights[]`
- `one_line_entry` (Skills): `label`, `details`
- `summary`: list of strings
- `phone`: `+CC-NNN-NNN-NNNN`, no `tel:`. Display via `design.header.connections.phone_number_format` (default `national`).
- `social_networks[].network` enum: `GitHub`, `YouTube`, `ORCID`, `GoogleScholar`, `LinkedIn`, … `username` is the raw handle (NO leading `@` for YouTube; rendercv adds it).

Design knobs proven to hold 2 pages with `engineeringresumes`:

```yaml
design:
  theme: engineeringresumes
  typography:
    line_spacing: 0.55em
  sections:
    space_between_regular_entries: 0.32cm
    space_between_text_based_entries: 0.10cm
  entries:
    summary: {space_above: 0.05cm}
    highlights: {space_above: 0.05cm, space_between_items: 0.05cm}
```

## Common rendering traps

| Symptom | Cause | Fix |
| --- | --- | --- |
| `;` disappears after `**bold**` | typst drops orphan punctuation after closing `**` | Replace `;` with ` — `, `,`, or ` · ` |
| Paragraph splits into separate lines | block scalar used `|-` | Switch to `>-` (folded) |
| YouTube handle doubled (`@@x`) | username included leading `@` | Drop the `@` |
| Google Scholar link garbled | username included `citations?user=` prefix | Use the raw Scholar author-ID, or omit |
| Orphan near-empty page 3 | a section grew | Tighten copy, then lower the four spacing knobs. Never drop a section. |
| Phone shows `tel:+…` | `tel:` URI prefix retained | Use `+CC-NNN-NNN-NNNN` |

## Research crawler — fields it returns (in data/research.json)

| Source | Function | Key fields |
| --- | --- | --- |
| OJS | `fetch_ojs_downloads` | `ojs.downloads_int`, `ojs.downloads_raw` |
| Semantic Scholar | `fetch_semantic_scholar` | `semantic_scholar.citation_count`, `…citing_papers[]` |
| OpenCitations | `fetch_opencitations` | `opencitations.count`, `…citing_dois[]` |
| Crossref Events | `fetch_crossref_events` | `crossref_events.events[]` |
| Google Scholar (opt) | `fetch_google_scholar` | `google_scholar.citations` or `available:false` |
| GitHub | `fetch_github` | `github.totals.{stars,forks,non_fork_repos}`, `github.top_repos[]` |
| Unpaywall→arXiv→pypdf | `enrich_citing_paper` | per cite: `pdf_status`, `reference_entry.{label,ref_pages}`, `contexts[].{page,snippet}` |

`pdf_status`: `ok` (excerpt extracted) · `ref_only` (in refs, no inline) ·
`no_match` (PDF has no mention — likely S2 false positive) · `not_oa` (paywalled,
e.g. IEEE) · `fetch_failed` (blocked, e.g. MDPI/ACM behind Akamai). Sources are
config in `profile.yaml`'s `research_sources.publications[].signatures`.

## When the user asks for…

| Request | Action |
| --- | --- |
| "Make a CV for me / for X" | STOP gate → get `profile.yaml` → run crawler → author `cv_data.yaml` → render. |
| "Refresh the numbers" | Re-run crawler, diff `data/research.json`, reconcile every moved figure in `cv_data.yaml`, re-render. |
| "Add a job / publication / project" | Edit `profile.yaml`. If it has a DOI or repo, re-run the crawler so its metrics enter `research.json`; then re-author `cv_data.yaml`. |
| "Translate to Vietnamese" | Out of scope; English-only by current decision. |
| "Switch theme" | Render once with the new theme, then re-tune the four spacing knobs for 2 pages. |
| "It overflows to 3 pages" | Tighten copy first, then lower the spacing knobs. Never drop a section. |

## Communication

Code and `cv_data.yaml` content stay English. User-facing replies follow the
user's language (Vietnamese is typical here).

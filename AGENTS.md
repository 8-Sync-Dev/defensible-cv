# AGENTS.md — entry point for AI coding agents

You are working in **defensible-cv**: a profile-driven pipeline that turns one
input file (`profile.yaml`) into a recruiter-ready, 2-page developer CV PDF where
**every number is verified live** and committed to `data/research.json`.

## Read this, then obey the skill

1. **`agents/skills/defensible-cv/SKILL.md`** — the authoritative playbook.
   Read it fully before touching anything. It defines the pipeline, the STOP
   gate, the mandatory rules, the rendercv schema, and the rendering traps.
2. This `AGENTS.md` only routes you there and states the non-negotiables below.

Priority when instructions conflict: **the user's current request → this
AGENTS.md → `SKILL.md` → general best practice.**

## Non-negotiables

- **STOP gate.** If `profile.yaml` does not exist at the repo root, STOP and ask
  the user to provide it (`cp profile.example.yaml profile.yaml`, then fill it).
  NEVER invent a person, reuse the committed example as if it were the user's,
  or fabricate numbers.
- **Numbers must be traceable.** Every figure on the CV (downloads, citations,
  stars, forks) must come from `data/research.json`. If it isn't there, run the
  crawler first.
- **2 pages, English, engineeringresumes theme.** Re-render and visually verify
  after every content change. Never drop a section to fit — tighten copy / lower
  the spacing knobs documented in the skill.
- Do not commit `.venv/`, `data/.cache/`, or `rendercv_output/` (already gitignored).

## The pipeline

```
profile.yaml  ──►  scripts/research.py  ──►  data/research.json + research.md
                                                      │
                                                      ▼
                                  cv_data.yaml  ──►  rendercv  ──►  PDF (2 pages)
```

## Canonical commands

```bash
# 1. Refresh verified metrics (reads profile.yaml's research_sources only)
uv run --with requests --with pyyaml --with pypdf \
  python scripts/research.py --no-scholar

# 2. Render the CV to PDF (no venv needed)
uv run --with "rendercv[full]>=2.8" rendercv render cv_data.yaml

# 3. ATS sanity check
pdftotext rendercv_output/*CV.pdf - | head -80
```

## How a human invokes you

Typical prompts that should trigger this skill:

- *"Build my CV from `profile.yaml` using the defensible-cv skill, refresh the
  metrics, and render the PDF."*
- *"I want a CV — here's my info: …. Follow `AGENTS.md` + the defensible-cv
  skill, write `profile.yaml`, then build it."*
- *"Refresh the CV numbers and re-render."*

If `profile.yaml` is missing when you receive any of these, your first action is
to ask for it — not to start writing.

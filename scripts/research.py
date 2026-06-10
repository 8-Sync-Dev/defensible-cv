#!/usr/bin/env python3
"""
Research metrics crawler for cv_data.yaml claims.

Collects, for each tracked publication and the candidate's GitHub footprint:
  - OJS download counts (jte.edu.vn — Open Journal Systems)
  - Semantic Scholar citation count and full citing-paper list
  - OpenCitations citation count and citing DOIs
  - Crossref Event Data mentions (best-effort, optional)
  - Google Scholar citation count (optional, often rate-limited; gated by --no-scholar)
  - GitHub aggregate stars/forks and per-repo metrics

Writes two artefacts:
  - data/research.json : machine-readable snapshot, used by cv_data.yaml
  - data/research.md   : recruiter-friendly summary

Cache layer at data/.cache/<sha1>.json with 24h TTL to be friendly to public APIs.
"""

from __future__ import annotations

import argparse

import hashlib
import json

import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / ".cache"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h

USER_AGENT = "cv-research-crawler/1.0 (+https://github.com/8syncdev)"

# ---- Profile loading --------------------------------------------------------
#
# The crawler is PROFILE-DRIVEN: identity and research sources live in a YAML
# profile (default `profile.yaml`), never hardcoded. This keeps the script
# reusable for any person — fill `profile.example.yaml`, point `--profile` at
# it, and the same pipeline researches their footprint.

DEFAULT_PROFILE_PATH = ROOT / "profile.yaml"


def load_profile(path: pathlib.Path) -> dict[str, Any]:
    """Load the person-specific profile that drives the crawler.

    The profile is the single source of identity + research sources. Without it
    the crawler has nothing to research, so this is a hard stop (exit 2) with a
    pointer to the template — never a silent fallback to someone else's data.
    """
    if not path.exists():
        sys.stderr.write(
            f"ERROR: profile not found at {path}\n"
            "  This crawler is profile-driven. Copy the template and fill it in:\n"
            f"      cp profile.example.yaml {path.name}\n"
            "  then set research_sources.github_user and research_sources.publications.\n"
        )
        raise SystemExit(2)
    profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rs = profile.get("research_sources") or {}
    if not rs.get("github_user") and not rs.get("publications"):
        sys.stderr.write(
            f"ERROR: {path} defines no research_sources.github_user or .publications.\n"
            "  Fill at least one so the crawler has something to verify.\n"
        )
        raise SystemExit(2)
    return profile

# Venue / DOI-prefix → publisher label, used to flag "high-impact" citing papers
# so a recruiter can see a citation is e.g. an IEEE or ACM paper at a glance.
HIGH_IMPACT_DOI_PREFIXES = {
    "10.1109": "IEEE",
    "10.1145": "ACM",
    "10.1016": "Elsevier",
    "10.1007": "Springer",
    "10.1038": "Nature",
    "10.1126": "Science",
    "10.3390": "MDPI",
    "10.1142": "World Scientific",
}
HIGH_IMPACT_CITATION_THRESHOLD = 50

# ---- HTTP / cache helpers ---------------------------------------------------


def _cache_path(url: str) -> pathlib.Path:
    return CACHE_DIR / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")


def http_get(url: str, *, accept: str = "application/json", refresh: bool = False) -> tuple[int, str]:
    """GET `url`, returning (status, body). Caches body+status for CACHE_TTL_SECONDS.

    Retries on 429 with a short backoff (Semantic Scholar throttles aggressively
    on its anonymous tier). Errors and 429 responses are never cached so the
    next run can succeed.
    """
    cp = _cache_path(url)
    if not refresh and cp.exists():
        try:
            payload = json.loads(cp.read_text())
            if time.time() - payload.get("ts", 0) < CACHE_TTL_SECONDS:
                return int(payload["status"]), payload["body"]
        except (json.JSONDecodeError, OSError, ValueError):
            pass  # fall through to refetch

    status, body = 0, ""
    for attempt in range(6):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": accept,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                body = resp.read().decode("utf-8", errors="replace")
                break
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            if status == 429 and attempt < 5:
                time.sleep(5 * (attempt + 1))  # 5s, 10s, 15s, 20s, 25s; then give up
                continue
            break
        except (urllib.error.URLError, TimeoutError) as exc:
            return 0, f"__error__: {exc!r}"

    # Only cache successful or 4xx-non-429 responses; transient throttles stay fresh
    if status and status != 429:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"ts": time.time(), "status": status, "body": body}))
    return status, body


# ---- Fetchers ---------------------------------------------------------------


def fetch_ojs_downloads(article_url: str, *, refresh: bool = False) -> dict[str, Any]:
    """
    Extract Open Journal Systems download total.

    OJS embeds usage stats as inline JS:
        ...,"label":"All Downloads","color":"...","total":1074};
    We pull the integer directly from that payload — robust because the
    visible <span id="totalDownloadCount"> is filled in by client-side JS.
    """
    status, body = http_get(article_url, accept="text/html", refresh=refresh)
    if status != 200:
        return {"url": article_url, "status": status, "downloads_int": None, "downloads_raw": None}

    match = re.search(
        r'"label"\s*:\s*"All Downloads"\s*,\s*"color"\s*:\s*"[^"]*"\s*,\s*"total"\s*:\s*(\d+)',
        body,
    )
    if not match:
        return {"url": article_url, "status": status, "downloads_int": None, "downloads_raw": None}

    n = int(match.group(1))
    return {
        "url": article_url,
        "status": status,
        "downloads_int": n,
        "downloads_raw": _humanize_downloads(n),
    }


def _humanize_downloads(n: int) -> str:
    if n >= 1_000_000:
        return f"{n // 1_000_000}M"
    if n >= 1_000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(n)

# ---- PDF + citation-context module ----------------------------------------
#
# For each citing paper we try to (a) locate an open-access PDF via Unpaywall
# / arXiv / Semantic Scholar, (b) extract per-page text, (c) find the inline
# location where the citing paper actually references our work, and (d) keep
# the surrounding excerpt + page number.
#
# This is what makes the CV defensible: we don't just say "cited by ACM /
# IEEE" — we surface "[ref 43], page 7, '...AST-based approaches such as
# those used in LeetCode [43] are common in educational settings...'".

PDF_DIR = CACHE_DIR / "pdfs"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Substrings that identify each tracked paper inside another author's PDF /
# reference list. Populated at runtime from the profile's publications
# (`research_sources.publications[].signatures`); empty until `collect()` runs.
OUR_PAPER_SIGNATURES: dict[str, list[str]] = {}


def http_get_bytes(url: str, *, refresh: bool = False) -> tuple[int, bytes]:
    """Like http_get but returns raw bytes, cached on disk under data/.cache/pdfs."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    meta_path = PDF_DIR / (h + ".meta.json")
    body_path = PDF_DIR / (h + ".bin")
    if not refresh and meta_path.exists() and body_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if time.time() - meta.get("ts", 0) < CACHE_TTL_SECONDS:
                return int(meta["status"]), body_path.read_bytes()
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    status, body = 0, b""
    for attempt in range(4):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/pdf, */*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = resp.status
                body = resp.read()
                break
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body = exc.read()
            except Exception:
                body = b""
            if status == 429 and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            break
        except (urllib.error.URLError, TimeoutError):
            return 0, b""

    if status == 200 and body:
        body_path.write_bytes(body)
        meta_path.write_text(json.dumps({"ts": time.time(), "status": status, "size": len(body)}))
    return status, body


def resolve_pdf_url(citation: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
    """
    Pick an open-access PDF URL for `citation`. Returns
    {url, source, status} where status ∈ {"ok", "not_oa", "no_doi"}.
    """
    doi = citation.get("doi")
    arxiv = citation.get("arxiv")

    # 1) arXiv is the cheapest and most permissive
    if arxiv:
        # ArXiv stores ids like "2406.13253" or "2507.19390"
        clean = re.sub(r"^arXiv:", "", arxiv, flags=re.I)
        return {"url": f"https://arxiv.org/pdf/{clean}", "source": "arxiv", "status": "ok"}

    if not doi:
        return {"url": None, "source": None, "status": "no_doi"}

    # 2) Unpaywall
    unpaywall_url = (
        f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='')}?email=research@cv.local"
    )
    s, body = http_get(unpaywall_url, refresh=refresh)
    if s == 200:
        try:
            data = json.loads(body)
            best = data.get("best_oa_location") or {}
            pdf_url = best.get("url_for_pdf") or best.get("url")
            if pdf_url:
                return {"url": pdf_url, "source": "unpaywall", "status": "ok"}
        except json.JSONDecodeError:
            pass

    return {"url": None, "source": None, "status": "not_oa"}


def download_pdf(url: str, *, refresh: bool = False) -> pathlib.Path | None:
    """Download `url`. Returns local Path if the result looks like a PDF, else None."""
    status, body = http_get_bytes(url, refresh=refresh)
    if status != 200 or len(body) < 1024:
        return None
    if not body.startswith(b"%PDF"):
        return None
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return PDF_DIR / (h + ".bin")


def extract_pdf_pages(path: pathlib.Path) -> list[str]:
    """Return per-page text. Empty list if pypdf isn't installed or the PDF is corrupt."""
    try:
        import pypdf  # type: ignore
    except ImportError:
        return []
    try:
        reader = pypdf.PdfReader(str(path))
    except Exception:
        return []
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return pages


def find_our_reference(pages: list[str], our_doi: str) -> dict[str, Any] | None:
    """
    Locate our paper inside `pages`'s reference list and return:
        {
          "label": "[43]" | "Nguyen and Hoang, 2024" | ...,
          "kind":  "numbered" | "author-year",
          "ref_text": "<verbatim reference entry>",
          "ref_pages": [N, ...]   # which pages contain the entry
        }
    """
    signatures = OUR_PAPER_SIGNATURES.get(our_doi, [our_doi])
    hits: list[tuple[int, int, str]] = []  # (page_idx, char_idx_of_signature, matched_sig)
    for i, text in enumerate(pages):
        for sig in signatures:
            j = text.find(sig)
            if j >= 0:
                hits.append((i, j, sig))
                break
    if not hits:
        return None

    page_idx, idx, _sig = hits[0]
    text = pages[page_idx]
    # Look backwards up to 400 chars for the start of this reference entry.
    chunk = text[max(0, idx - 400):idx + 350]

    # Pattern 1: numbered reference — `[N]` or `N.` immediately preceding the entry
    # Use a non-greedy lookbehind via reverse search on the chunk.
    rev = chunk[: chunk.find(_sig) if chunk.find(_sig) >= 0 else len(chunk)]
    m_num = None
    for m in re.finditer(r"\[(\d{1,3})\]\s*", rev):
        m_num = m
    if m_num:
        return {
            "label": f"[{m_num.group(1)}]",
            "label_num": int(m_num.group(1)),
            "kind": "numbered",
            "ref_text": chunk.strip().replace("\n", " "),
            "ref_pages": [p + 1 for p, _, _ in hits],
        }

    # Pattern 2: "12. " or "12) " style numbering at start-of-line
    m_dot = None
    for m in re.finditer(r"(?:^|\n)\s*(\d{1,3})[.)]\s", rev):
        m_dot = m
    if m_dot:
        return {
            "label": f"{m_dot.group(1)}",
            "label_num": int(m_dot.group(1)),
            "kind": "numbered-dot",
            "ref_text": chunk.strip().replace("\n", " "),
            "ref_pages": [p + 1 for p, _, _ in hits],
        }

    # Pattern 3: author-year — fall back to a "Nguyen ... 2024" probe
    return {
        "label": "Nguyen",
        "kind": "author-year",
        "ref_text": chunk.strip().replace("\n", " "),
        "ref_pages": [p + 1 for p, _, _ in hits],
    }


def find_inline_contexts(
    pages: list[str], ref_info: dict[str, Any], our_doi: str, *, limit: int = 6
) -> list[dict[str, Any]]:
    """
    Find places in the body where the citing paper invokes our reference.
    Excludes pages that themselves contain our paper's reference entry
    (because those are the reference-list pages, not body citations).
    """
    excluded = set(ref_info.get("ref_pages") or [])
    signatures = OUR_PAPER_SIGNATURES.get(our_doi, [our_doi])

    contexts: list[dict[str, Any]] = []

    if ref_info["kind"] in ("numbered", "numbered-dot"):
        num = ref_info["label_num"]
        # Inline numeric citation: [N], [12,38], [12, 38], [12-15], etc.
        # We match a single number INSIDE a bracket that may list multiple refs.
        inner_pat = re.compile(
            r"\[(?:[^\[\]]*?[,\-\u2013\s])?"   # optional preceding numbers / separators
            + str(num)
            + r"(?:[,\-\u2013\s][^\[\]]*?)?\]"  # optional trailing numbers
        )
        # Reject the page if our reference-entry signature appears too close
        for i, text in enumerate(pages):
            if (i + 1) in excluded:
                continue
            for m in inner_pat.finditer(text):
                # Sanity: make sure this isn't a page or figure number.
                # Require letters/words in surrounding context.
                start = max(0, m.start() - 350)
                end = min(len(text), m.end() + 250)
                snippet = text[start:end].replace("\n", " ")
                snippet = re.sub(r"\s+", " ", snippet).strip()
                if not re.search(r"[A-Za-z]{3,}", snippet):
                    continue
                # Skip false positives where our signature is right next to the match
                if any(sig in snippet for sig in signatures):
                    continue
                contexts.append(
                    {
                        "page": i + 1,
                        "match": m.group(),
                        "snippet": snippet,
                    }
                )
                if len(contexts) >= limit:
                    return contexts
    else:
        # author-year fallback: look for "Nguyen ... 2024" near each other
        pat = re.compile(r"Nguyen[^\n.;()]{0,80}?(?:and Hoang|et al\.?)?[^\n.;()]{0,30}?\b2024\b")
        for i, text in enumerate(pages):
            if (i + 1) in excluded:
                continue
            for m in pat.finditer(text):
                snippet = text[max(0, m.start() - 250):m.end() + 250].replace("\n", " ")
                snippet = re.sub(r"\s+", " ", snippet).strip()
                contexts.append({"page": i + 1, "match": m.group(), "snippet": snippet})
                if len(contexts) >= limit:
                    return contexts

    return contexts


def enrich_citing_paper(citation: dict[str, Any], our_doi: str, *, refresh: bool) -> dict[str, Any]:
    """Attach PDF + citation-context evidence to a citing-paper record."""
    res = dict(citation)
    pdf_lookup = resolve_pdf_url(citation, refresh=refresh)
    res["pdf_url"] = pdf_lookup.get("url")
    res["pdf_source"] = pdf_lookup.get("source")
    res["pdf_status"] = pdf_lookup.get("status")
    res["contexts"] = []
    res["reference_entry"] = None
    res["pages_total"] = None

    if not pdf_lookup.get("url"):
        return res

    path = download_pdf(pdf_lookup["url"], refresh=refresh)
    if path is None:
        res["pdf_status"] = "fetch_failed"
        return res

    pages = extract_pdf_pages(path)
    res["pages_total"] = len(pages)
    if not pages:
        res["pdf_status"] = "extract_failed"
        return res

    ref = find_our_reference(pages, our_doi)
    if ref is None:
        # The PDF doesn't actually mention our paper — flag as a false positive
        # in upstream citation data. Keep the record so the report shows it.
        res["pdf_status"] = "no_match"
        return res

    res["reference_entry"] = ref
    res["contexts"] = find_inline_contexts(pages, ref, our_doi)
    res["pdf_status"] = "ok" if res["contexts"] else "ref_only"
    return res



def fetch_semantic_scholar(doi: str, *, refresh: bool = False) -> dict[str, Any]:
    """Fetch paper metadata + citation list from Semantic Scholar Graph API.

    Polite pacing: a small sleep between the two endpoint calls keeps us
    under the 100 req/5min anonymous tier even when iterating over papers.
    """
    time.sleep(1.2)
    base = "https://api.semanticscholar.org/graph/v1/paper/DOI:"
    paper_url = (
        base
        + urllib.parse.quote(doi, safe="")
        + "?fields=title,year,venue,citationCount,influentialCitationCount,authors,externalIds"
    )
    status, body = http_get(paper_url, refresh=refresh)
    if status != 200:
        return {"status": status, "available": False, "raw": body[:200]}

    paper = json.loads(body)

    cit_url = (
        base
        + urllib.parse.quote(doi, safe="")
        + "/citations?fields=title,venue,year,authors,externalIds,citationCount&limit=100"
    )
    cs_status, cs_body = http_get(cit_url, refresh=refresh)
    citations: list[dict[str, Any]] = []
    if cs_status == 200:
        for item in json.loads(cs_body).get("data", []):
            citing = item.get("citingPaper") or {}
            citations.append(
                {
                    "title": citing.get("title"),
                    "venue": citing.get("venue"),
                    "year": citing.get("year"),
                    "citationCount": citing.get("citationCount", 0),
                    "doi": (citing.get("externalIds") or {}).get("DOI"),
                    "arxiv": (citing.get("externalIds") or {}).get("ArXiv"),
                    "authors": [a.get("name") for a in (citing.get("authors") or [])],
                }
            )

    return {
        "status": status,
        "available": True,
        "paper_id": paper.get("paperId"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "citation_count": paper.get("citationCount", 0),
        "influential_citation_count": paper.get("influentialCitationCount", 0),
        "authors": [a.get("name") for a in (paper.get("authors") or [])],
        "citing_papers": citations,
    }


def fetch_opencitations(doi: str, *, refresh: bool = False) -> dict[str, Any]:
    """Independent citation count from OpenCitations COCI/Meta."""
    count_url = f"https://api.opencitations.net/index/v2/citation-count/doi:{doi}"
    s, body = http_get(count_url, refresh=refresh)
    count = 0
    if s == 200:
        try:
            payload = json.loads(body)
            if payload:
                count = int(payload[0].get("count", 0))
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    cits_url = f"https://api.opencitations.net/index/v2/citations/doi:{doi}"
    s2, body2 = http_get(cits_url, refresh=refresh)
    citing_dois: list[str] = []
    if s2 == 200:
        try:
            for c in json.loads(body2):
                citing = c.get("citing", "")
                # citing is "omid:br/... doi:10.x/y"
                for part in citing.split():
                    if part.startswith("doi:"):
                        citing_dois.append(part[4:])
        except (json.JSONDecodeError, AttributeError):
            pass

    return {"status": s, "count": count, "citing_dois": citing_dois}


def fetch_crossref_events(doi: str, *, refresh: bool = False) -> dict[str, Any]:
    """Crossref Event Data — picks up tweet/wiki/news mentions if any."""
    obj = urllib.parse.quote(f"https://doi.org/{doi}", safe="")
    url = f"https://api.eventdata.crossref.org/v1/events?obj-id={obj}&rows=50"
    s, body = http_get(url, refresh=refresh)
    if s != 200:
        return {"status": s, "events": []}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"status": s, "events": []}
    events = []
    for ev in (payload.get("message") or {}).get("events", []) or []:
        events.append(
            {
                "source": ev.get("source_id"),
                "subj": (ev.get("subj") or {}).get("title") or (ev.get("subj") or {}).get("pid"),
                "occurred_at": ev.get("occurred_at"),
            }
        )
    return {"status": s, "events": events}


def fetch_google_scholar(title: str) -> dict[str, Any]:
    """Best-effort Scholar lookup. scholarly is fragile (CAPTCHA) — soft-fail."""
    try:
        from scholarly import scholarly  # type: ignore
    except ImportError:
        return {"available": False, "reason": "scholarly not installed"}
    try:
        search_iter = scholarly.search_pubs(title)
        first = next(search_iter, None)
        if not first:
            return {"available": False, "reason": "no result"}
        bib = first.get("bib") or {}
        return {
            "available": True,
            "citations": first.get("num_citations", 0),
            "cluster_id": first.get("cluster_id"),
            "url": first.get("pub_url") or first.get("eprint_url"),
            "title": bib.get("title"),
            "year": bib.get("pub_year"),
        }
    except Exception as exc:  # scholarly raises a zoo of exceptions; treat all as "blocked"
        return {"available": False, "reason": f"blocked: {type(exc).__name__}: {exc!s}"[:200]}


def fetch_github(user: str, *, refresh: bool = False) -> dict[str, Any]:
    """User profile + paginated repos. No auth — sufficient for ≤100 public repos."""
    profile_url = f"https://api.github.com/users/{user}"
    s, body = http_get(profile_url, refresh=refresh)
    if s != 200:
        return {"status": s, "available": False}

    profile = json.loads(body)

    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        repos_url = f"https://api.github.com/users/{user}/repos?per_page=100&page={page}&type=owner&sort=updated"
        rs, rbody = http_get(repos_url, refresh=refresh)
        if rs != 200:
            break
        batch = json.loads(rbody)
        if not batch:
            break
        for r in batch:
            if r.get("fork"):
                continue  # exclude forks; recruiters want original work
            repos.append(
                {
                    "name": r.get("name"),
                    "full_name": r.get("full_name"),
                    "description": r.get("description"),
                    "stars": r.get("stargazers_count", 0),
                    "forks": r.get("forks_count", 0),
                    "watchers": r.get("watchers_count", 0),
                    "language": r.get("language"),
                    "topics": r.get("topics") or [],
                    "url": r.get("html_url"),
                    "updated_at": r.get("updated_at"),
                    "archived": r.get("archived", False),
                }
            )
        if len(batch) < 100:
            break
        page += 1
        if page > 10:  # safety net
            break

    repos.sort(key=lambda r: (r["stars"], r["forks"]), reverse=True)

    languages: dict[str, int] = {}
    for r in repos:
        if r["language"]:
            languages[r["language"]] = languages.get(r["language"], 0) + 1

    return {
        "status": s,
        "available": True,
        "login": profile.get("login"),
        "name": profile.get("name"),
        "bio": profile.get("bio"),
        "location": profile.get("location"),
        "public_repos": profile.get("public_repos"),
        "followers": profile.get("followers"),
        "following": profile.get("following"),
        "hireable": profile.get("hireable"),
        "created_at": profile.get("created_at"),
        "html_url": profile.get("html_url"),
        "totals": {
            "stars": sum(r["stars"] for r in repos),
            "forks": sum(r["forks"] for r in repos),
            "non_fork_repos": len(repos),
        },
        "top_repos": repos[:10],
        "all_repos": repos,
        "languages": sorted(languages.items(), key=lambda kv: -kv[1]),
    }


# ---- Ranking ---------------------------------------------------------------


def rank_citing_papers(citations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate each citing paper with an `is_high_impact` flag + reason."""
    ranked: list[dict[str, Any]] = []
    for c in citations:
        reasons: list[str] = []
        prefix = (c.get("doi") or "").split("/", 1)[0]
        publisher = HIGH_IMPACT_DOI_PREFIXES.get(prefix)
        if publisher:
            reasons.append(publisher)
        if (c.get("citationCount") or 0) >= HIGH_IMPACT_CITATION_THRESHOLD:
            reasons.append(f"{c.get('citationCount')}+ cites")
        venue = (c.get("venue") or "").lower()
        for token in ("ieee", "acm", "elsevier", "springer", "nature"):
            if token in venue and (not publisher or publisher.lower() != token):
                reasons.append(token.upper())
                break
        out = dict(c)
        out["is_high_impact"] = bool(reasons)
        out["impact_tags"] = reasons
        ranked.append(out)
    # Stable sort: high-impact first, then by citation count
    ranked.sort(key=lambda c: (not c["is_high_impact"], -(c.get("citationCount") or 0)))
    return ranked


# ---- Orchestration ---------------------------------------------------------


def collect(*, refresh: bool, scholar: bool, profile: dict[str, Any]) -> dict[str, Any]:
    sources = profile.get("research_sources") or {}
    publications = sources.get("publications") or []
    github_user = sources.get("github_user")

    # Populate the module-global signature map from the profile so the PDF
    # citation-context matcher knows what substrings identify each paper.
    OUR_PAPER_SIGNATURES.clear()
    for pub in publications:
        OUR_PAPER_SIGNATURES[pub["doi"]] = pub.get("signatures") or [pub["doi"]]

    pubs_out = []
    for pub in publications:
        ojs = (
            fetch_ojs_downloads(pub["ojs_url"], refresh=refresh)
            if pub.get("ojs_url")
            else {"url": None, "status": None, "downloads_int": None, "downloads_raw": None}
        )
        s2 = fetch_semantic_scholar(pub["doi"], refresh=refresh)
        oc = fetch_opencitations(pub["doi"], refresh=refresh)
        ev = fetch_crossref_events(pub["doi"], refresh=refresh)
        gs = fetch_google_scholar(pub["title"]) if scholar else {"available": False, "reason": "skipped (--no-scholar)"}
        citing = rank_citing_papers(s2.get("citing_papers", []) if s2.get("available") else [])
        # Enrich each citing paper with PDF + verbatim citation context where possible.
        citing = [enrich_citing_paper(c, pub["doi"], refresh=refresh) for c in citing]
        pubs_out.append(
            {
                **pub,
                "ojs": ojs,
                "semantic_scholar": {**{k: v for k, v in s2.items() if k != "citing_papers"}, "citing_papers": citing},
                "opencitations": oc,
                "crossref_events": ev,
                "google_scholar": gs,
                "headline_citation_count": max(
                    s2.get("citation_count", 0) if s2.get("available") else 0,
                    oc.get("count", 0),
                    gs.get("citations", 0) if gs.get("available") else 0,
                ),
            }
        )

    gh = fetch_github(github_user, refresh=refresh) if github_user else {"available": False, "status": None}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": (profile.get("identity") or {}).get("name", ""),
        "publications": pubs_out,
        "github": gh,
    }


# ---- Markdown report --------------------------------------------------------


def render_markdown(snapshot: dict[str, Any]) -> str:
    out: list[str] = []
    name = snapshot.get("name") or "candidate"
    out.append(f"# Research metrics — {name}")
    out.append(f"_Last refreshed: {snapshot['generated_at']}_")
    out.append("")
    out.append("## Publications")
    for pub in snapshot["publications"]:
        out.append("")
        out.append(f"### {pub['title']}")
        out.append(f"- DOI: [{pub['doi']}](https://doi.org/{pub['doi']})")
        if pub.get("journal"):
            out.append(f"- Journal: {pub['journal']} ({pub.get('year', 'n.d.')})")
        if pub.get("authors_display"):
            out.append(f"- Authors: {pub['authors_display']}")
        dl = pub["ojs"]
        if dl.get("downloads_int") is not None:
            out.append(f"- OJS downloads: **{dl['downloads_int']:,}** (raw `{dl['downloads_raw']}`)")
        s2 = pub["semantic_scholar"]
        oc = pub["opencitations"]
        gs = pub["google_scholar"]
        out.append(
            "- Citations: "
            + " · ".join(
                [
                    f"**{s2.get('citation_count', 0)}** (Semantic Scholar)" if s2.get("available") else "n/a (Semantic Scholar)",
                    f"**{oc.get('count', 0)}** (OpenCitations)",
                    (
                        f"**{gs.get('citations', 0)}** (Google Scholar)"
                        if gs.get("available")
                        else f"n/a (Google Scholar — {gs.get('reason', 'unavailable')})"
                    ),
                ]
            )
        )
        if s2.get("available") and s2.get("influential_citation_count"):
            out.append(f"- Influential citations (S2): **{s2['influential_citation_count']}**")
        citing = s2.get("citing_papers", [])
        ctx_ok = [c for c in citing if c.get("pdf_status") == "ok"]
        out.append(
            f"- Citing papers with extracted in-text excerpts: "
            f"**{len(ctx_ok)} of {len(citing)}** (others paywalled or not in Unpaywall)."
        )
        if citing:
            out.append("")
            out.append("#### Per-citation evidence")
            out.append("")
            for c in citing:
                tags = ", ".join(c.get("impact_tags") or []) or "—"
                year = c.get("year") or "n.d."
                venue = c.get("venue") or "—"
                doi = c.get("doi")
                link = f"[{doi}](https://doi.org/{doi})" if doi else "_no DOI_"
                authors = ", ".join((c.get("authors") or [])[:3])
                if len(c.get("authors") or []) > 3:
                    authors += " et al."
                out.append(f"##### **[{tags}]** {c.get('title')}")
                out.append(f"- Venue: *{venue}*, {year} · {link}")
                if authors:
                    out.append(f"- Authors: {authors}")
                ref = c.get("reference_entry")
                pdf_status = c.get("pdf_status")
                if pdf_status == "ok" and ref:
                    out.append(
                        f"- They list our paper as reference **{ref['label']}** "
                        f"(see their page {ref['ref_pages']})."
                    )
                    out.append("- In-text excerpts where they invoke our work:")
                    for ex in c.get("contexts", [])[:4]:
                        out.append(
                            f'  - p.{ex["page"]} — _"…{ex["snippet"]}…"_'
                        )
                elif pdf_status == "ref_only" and ref:
                    out.append(
                        f"- Our paper appears in their reference list as **{ref['label']}** "
                        f"(p.{ref['ref_pages']}), but the body text uses an unrecognised inline format."
                    )
                elif pdf_status == "no_match":
                    out.append(
                        "- _PDF retrieved but our paper not found in the body — likely a "
                        "false-positive citation reported by Semantic Scholar._"
                    )
                elif pdf_status in ("not_oa", "fetch_failed", "extract_failed"):
                    out.append(
                        f"- _Full-text excerpt unavailable (status: `{pdf_status}`). "
                        "Verified via Semantic Scholar / OpenCitations citation graph._"
                    )
                else:
                    out.append("- _Status unknown._")
                out.append("")

    gh = snapshot["github"]
    out.append("")
    out.append("## Open-source impact")
    if not gh.get("available"):
        out.append("- GitHub data unavailable.")
    else:
        totals = gh["totals"]
        out.append(
            f"- User [`@{gh['login']}`]({gh['html_url']}) — "
            f"**{totals['stars']}★** · **{totals['forks']}** forks · "
            f"{totals['non_fork_repos']} non-fork repos · "
            f"{gh.get('public_repos')} public total · "
            f"{gh.get('followers')} followers"
        )
        if gh.get("location"):
            out.append(f"- Location: {gh['location']} · Hireable: {gh.get('hireable')}")
        out.append("")
        out.append("### Top repositories")
        for r in gh["top_repos"]:
            desc = r.get("description") or ""
            lang = f" · {r['language']}" if r.get("language") else ""
            out.append(f"- [{r['name']}]({r['url']}) — **{r['stars']}★ / {r['forks']}F**{lang} — {desc}")
        if gh.get("languages"):
            top_langs = ", ".join(f"{lang} ({n})" for lang, n in gh["languages"][:8])
            out.append("")
            out.append(f"### Language footprint\n{top_langs}")

    out.append("")
    return "\n".join(out) + "\n"


# ---- CLI -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--refresh", action="store_true", help="Ignore cache; refetch every URL.")
    p.add_argument("--no-scholar", action="store_true", help="Skip Google Scholar (use in CI).")
    p.add_argument("--print", action="store_true", dest="do_print", help="Print recruiter summary to stdout.")
    p.add_argument("--out-json", default=str(DATA_DIR / "research.json"))
    p.add_argument("--out-md", default=str(DATA_DIR / "research.md"))
    p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH), help="Path to the person profile YAML (default: profile.yaml).")
    args = p.parse_args(argv)

    profile = load_profile(pathlib.Path(args.profile))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = collect(refresh=args.refresh, scholar=not args.no_scholar, profile=profile)

    pathlib.Path(args.out_json).write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    md = render_markdown(snapshot)
    pathlib.Path(args.out_md).write_text(md)

    if args.do_print:
        sys.stdout.write(md)

    # Concise stderr summary so CI logs are useful
    print(
        f"[research] wrote {args.out_json} and {args.out_md}",
        file=sys.stderr,
    )
    for pub in snapshot["publications"]:
        s2 = pub["semantic_scholar"]
        oc = pub["opencitations"]
        dl = pub["ojs"]
        print(
            f"  {pub['doi']}  downloads={dl.get('downloads_int')}  "
            f"s2={s2.get('citation_count', 0) if s2.get('available') else 'n/a'}  "
            f"oc={oc.get('count', 0)}  high_impact={sum(1 for c in s2.get('citing_papers', []) if c.get('is_high_impact'))}",
            file=sys.stderr,
        )
    gh = snapshot["github"]
    if gh.get("available"):
        t = gh["totals"]
        print(
            f"  github @{gh['login']} stars={t['stars']} forks={t['forks']} non_fork_repos={t['non_fork_repos']}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

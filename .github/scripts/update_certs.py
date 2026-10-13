#!/usr/bin/env python3
"""Update the certifications block of README.md from the public Credly feed.

RF-08 / ADR-0001. Rewrites only the content between the markers
``<!-- CERTS:START -->`` and ``<!-- CERTS:END -->``; the rest of the README is
preserved. Designed to run on a schedule (and via workflow_dispatch) and to fail
the job WITHOUT committing anything if the Credly feed can't be read or looks wrong.

Data facts confirmed against the live feed (2026-10, user "nicolasmatos"):
- Endpoint: https://www.credly.com/users/<user>/badges.json  (public, no auth).
- Response shape: {"data": [...], "metadata": {... pagination ...}}.
- Pagination lives in ``metadata`` as ``next_page_url`` / ``total_pages`` / ``per``
  (``per`` is 48, not 50) — paginate off ``next_page_url``, do NOT guess a page size.
- Each badge has ``badge_template.type_category`` ("Certification", "Learning",
  "Experience") — only "Certification" counts toward the cert totals.
- Issue date is ``issued_at_date`` ("YYYY-MM-DD"); ``badge_template.level`` is one of
  "Advanced" (Pro/Specialty), "Intermediate" (Associate), "Foundational".

Oracle (4 certs) is NOT on Credly's public feed (Oracle uses its own platform), so the
Oracle count is read from the site's Worker endpoint (ORACLE_URL), which proxies Oracle's
external badges. Both AWS and Oracle are rendered as per-provider shields badges (D3:
badges by provider, no manually named list); the Oracle count is derived from the Worker's
``metadata.total_count`` — nothing hardcoded.

Note (ADR-0001 tension): ADR-0001 called for reading Credly direct and NOT depending on the
Worker. Oracle isn't on Credly's public feed, so the Worker is the only programmatic Oracle
source; this script therefore depends on the Worker for the Oracle count. If the Worker is
down the job fails WITHOUT committing (same policy as a Credly failure). Revisit the ADR if
this coupling becomes a problem.
"""
from __future__ import annotations

import os
import re
import sys
import json
import urllib.request
import urllib.error

CREDLY_USER = os.environ.get("CREDLY_USER", "nicolasmatos")
README_PATH = os.environ.get("README_PATH", "README.md")
START = "<!-- CERTS:START -->"
END = "<!-- CERTS:END -->"

CREDLY_PROFILE = f"https://www.credly.com/users/{CREDLY_USER}/badges"

# Oracle Cloud certs come from the site Worker (proxies Oracle external badges).
# Count is derived from the response's metadata.total_count — nothing hardcoded.
ORACLE_URL = os.environ.get("ORACLE_URL", "https://matos.dev.br/api/credly/external")

# Total AWS certifications in the "Golden Jacket" program (all-active goal).
GOLDEN_JACKET_TOTAL = 13

# Human label per Credly level value (for the AWS level summary line).
LEVEL_LABEL = {
    "Advanced": "Advanced",
    "Intermediate": "Associate",
    "Foundational": "Foundational",
}


def _fetch_paginated(url: str, base: str, source: str) -> tuple[list[dict], dict]:
    """Fetch all ``data`` rows from a Credly-shaped feed, following pagination.

    Both the Credly feed and the site Worker share the shape
    ``{"data": [...], "metadata": {... next_page_url ...}}``. Returns the full
    ``data`` list plus the LAST page's ``metadata`` (which carries total_count).
    Raises on any network / HTTP / JSON error so the caller aborts WITHOUT writing
    a partial or empty block to the README.
    """
    rows: list[dict] = []
    meta: dict = {}
    seen_pages = 0
    while url:
        req = urllib.request.Request(
            url, headers={"User-Agent": "gh-profile-cert-bot/1.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        data = payload.get("data")
        if not isinstance(data, list):
            raise ValueError(f"Unexpected {source} response: 'data' is not a list")
        rows.extend(data)
        meta = payload.get("metadata") or {}

        next_url = meta.get("next_page_url")
        if next_url and next_url.startswith("/"):  # normalize relative to absolute
            next_url = base + next_url
        url = next_url

        seen_pages += 1
        if seen_pages > 50:  # hard stop against a pagination loop
            raise RuntimeError(f"Aborting: too many {source} pages (possible loop)")
    return rows, meta


def fetch_badges(user: str) -> list[dict]:
    """Fetch all public AWS badges for ``user`` from the Credly public feed."""
    url = f"https://www.credly.com/users/{user}/badges.json"
    rows, _ = _fetch_paginated(url, "https://www.credly.com", "Credly")
    return rows


def fetch_oracle_count() -> int:
    """Derive the Oracle cert count from the Worker's metadata.total_count.

    Falls back to counting rows if total_count is missing. Raises on any
    network / HTTP / JSON error so the job fails without committing.
    """
    from urllib.parse import urlsplit
    parts = urlsplit(ORACLE_URL)
    base = f"{parts.scheme}://{parts.netloc}"
    rows, meta = _fetch_paginated(ORACLE_URL, base, "Oracle Worker")
    total = meta.get("total_count")
    if isinstance(total, int) and total >= 0:
        return total
    return len(rows)


def is_certification(badge: dict) -> bool:
    tmpl = badge.get("badge_template") or {}
    return tmpl.get("type_category") == "Certification"


def level_of(badge: dict) -> str:
    tmpl = badge.get("badge_template") or {}
    return tmpl.get("level") or ""


def _tier(badge: dict) -> str:
    """Readable tier from level + name (Credly lumps Pro+Specialty as 'Advanced')."""
    name = (badge.get("badge_template") or {}).get("name", "")
    if level_of(badge) == "Advanced":
        if "Specialty" in name:
            return "Specialty"
        if "Professional" in name:
            return "Professional"
        return "Advanced"
    return LEVEL_LABEL.get(level_of(badge), "Other")


def aws_level_summary(certs: list[dict]) -> str:
    """e.g. '2 Professional · 2 Specialty · 6 Associate · 2 Foundational'."""
    counts: dict[str, int] = {}
    for c in certs:
        tier = _tier(c)
        counts[tier] = counts.get(tier, 0) + 1
    order = ["Professional", "Specialty", "Advanced", "Associate", "Foundational", "Other"]
    parts = [f"{counts[k]} {k}" for k in order if counts.get(k)]
    return " · ".join(parts)


def build_markdown(aws_count: int, oracle_count: int, aws_summary: str) -> str:
    total = aws_count + oracle_count
    remaining = max(GOLDEN_JACKET_TOTAL - aws_count, 0)
    gj_note = (
        f"{aws_count}/{GOLDEN_JACKET_TOTAL} AWS — "
        + ("Golden Jacket complete! 🎉" if remaining == 0 else f"{remaining} remaining")
    )
    return "\n".join([
        f'<h3 align="center">🏆 {total}x Certified — {aws_count} AWS + {oracle_count} Oracle</h3>',
        f'<p align="center"><em>Pursuing the AWS Golden Jacket ({gj_note})</em></p>',
        "",
        '<p align="center">',
        f'  <a href="{CREDLY_PROFILE}" target="_blank" rel="noopener noreferrer">',
        f'    <img src="https://img.shields.io/badge/AWS%20Certified-{aws_count}x-FF9900?style=for-the-badge&logo=amazonwebservices&logoColor=white" alt="{aws_count}x AWS Certified">',
        "  </a>",
        f'  <a href="{CREDLY_PROFILE}" target="_blank" rel="noopener noreferrer">',
        f'    <img src="https://img.shields.io/badge/Oracle%20Cloud-{oracle_count}x-F80000?style=for-the-badge&logo=oracle&logoColor=white" alt="{oracle_count}x Oracle Cloud Certified">',
        "  </a>",
        f'  <a href="{CREDLY_PROFILE}" target="_blank" rel="noopener noreferrer">',
        f'    <img src="https://img.shields.io/badge/Golden%20Jacket-{aws_count}%2F{GOLDEN_JACKET_TOTAL}-FBBF24?style=for-the-badge&logo=amazonwebservices&logoColor=78350F" alt="Golden Jacket {aws_count}/{GOLDEN_JACKET_TOTAL} AWS">',
        "  </a>",
        "</p>",
        f'<p align="center">',
        f"  <sub>AWS: {aws_summary}&nbsp;&nbsp;|&nbsp;&nbsp;Oracle Cloud: {oracle_count} certifications</sub>",
        "</p>",
        '<p align="center">',
        f'  <a href="{CREDLY_PROFILE}" target="_blank" rel="noopener noreferrer">',
        '    <img src="https://img.shields.io/badge/View%20all%20on%20Credly-FF6B00?style=flat-square&logo=credly&logoColor=white" alt="View all badges on Credly">',
        "  </a>",
        "</p>",
    ])


def replace_block(readme: str, inner: str) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(readme):
        raise SystemExit(f"Markers {START} / {END} not found in {README_PATH}")
    return pattern.sub(f"{START}\n{inner}\n{END}", readme)


def main() -> int:
    net_errors = (urllib.error.URLError, urllib.error.HTTPError, ValueError, RuntimeError, OSError)

    try:
        badges = fetch_badges(CREDLY_USER)
    except net_errors as exc:
        print(f"ERROR reading Credly feed: {exc}. Aborting without changes.", file=sys.stderr)
        return 1

    certs = [b for b in badges if is_certification(b)]
    if not certs:
        print("No certifications (type_category=Certification) returned — aborting.", file=sys.stderr)
        return 1

    try:
        oracle_count = fetch_oracle_count()
    except net_errors as exc:
        print(f"ERROR reading Oracle Worker ({ORACLE_URL}): {exc}. Aborting without changes.",
              file=sys.stderr)
        return 1
    if oracle_count <= 0:
        print("Oracle Worker returned 0 certifications — aborting without changes.", file=sys.stderr)
        return 1

    aws_count = len(certs)
    md = build_markdown(aws_count, oracle_count, aws_level_summary(certs))

    with open(README_PATH, encoding="utf-8") as fh:
        readme = fh.read()
    updated = replace_block(readme, md)

    if updated != readme:
        with open(README_PATH, "w", encoding="utf-8") as fh:
            fh.write(updated)
        print(f"README updated: {aws_count} AWS + {oracle_count} Oracle "
              f"= {aws_count + oracle_count}x certified.")
    else:
        print("Certifications block already up to date — nothing to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

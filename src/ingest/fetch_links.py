"""
Stage 1 of the ingestion pipeline.

Downloads Stripe's llms.txt index, parses it into LinkEntry objects with
section metadata preserved, and saves the result to data/processed/
as a validated LinkCollection.

Usage:
    uv run python -m src.ingest.fetch_links
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
from loguru import logger
from pydantic import ValidationError

from src.config import settings
from src.ingest.models import LinkCollection, LinkEntry

# Where we save the raw llms.txt for lineage (so we can debug the parser later
# against the exact bytes we ingested, even if Stripe changes the source).
RAW_LLMS_PATH: Path = settings.raw_data_path / "llms.txt"

# Where we save the parsed, validated output.
OUTPUT_PATH: Path = settings.processed_data_path / "stripe_links.json"

# ----- Regex patterns -----
# A "## SectionName" line. Captures the section name.
SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$")

# A "- [Title](url): description" line (description optional).
# Group 1: title  |  Group 2: url  |  Group 3: description (may be empty)
LINK_PATTERN = re.compile(
    r"^\s*-\s+\[(.+?)\]\((.+?)\)\s*:?\s*(.*)$",
)


def fetch_llms_txt(url: str) -> str:
    """Download the llms.txt file and save a raw copy for lineage.

    Args:
        url: The URL of the llms.txt file to download.

    Returns:
        The text content of the file.

    Raises:
        httpx.HTTPError: If the download fails.
    """
    logger.info(f"Fetching llms.txt from {url}")

    timeout = httpx.Timeout(
        settings.http_timeout,
        connect=settings.http_connect_timeout,
    )
    headers = {"User-Agent": settings.http_user_agent}

    with httpx.Client(timeout=timeout, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()

    text = response.text
    logger.info(f"Downloaded {len(text):,} characters")

    # Persist the raw source for lineage / debugging.
    RAW_LLMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_LLMS_PATH.write_text(text, encoding="utf-8")
    logger.info(f"Saved raw source to {RAW_LLMS_PATH}")

    return text


def parse_llms_txt(text: str) -> list[LinkEntry]:
    """Parse llms.txt content into a list of LinkEntry objects.

    Walks the file line by line, tracking the current section as it
    encounters `## Section` headers. Each `- [Title](url): desc` line
    becomes a LinkEntry tagged with the most recently seen section.

    Args:
        text: The full text content of the llms.txt file.

    Returns:
        A list of validated LinkEntry objects.
    """
    entries: list[LinkEntry] = []
    current_section: str | None = None
    skipped_count = 0
    seen_urls: set[str] = set()

    for line_num, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()

        # Skip blank lines fast.
        if not line:
            continue

        # Check for section header first — it changes state.
        section_match = SECTION_PATTERN.match(line)
        if section_match:
            current_section = section_match.group(1).strip()
            logger.debug(f"Line {line_num}: section → {current_section!r}")
            continue

        # Check for link entry.
        link_match = LINK_PATTERN.match(line)
        if not link_match:
            # Not a section, not a link — skip silently (intro text, etc.).
            continue

        title, url, description = link_match.groups()
        title = title.strip()
        url = url.strip()
        description = description.strip()

        # Filter: we only want .md URLs.
        if not url.endswith(".md"):
            logger.debug(f"Line {line_num}: skipping non-.md URL: {url}")
            skipped_count += 1
            continue

        # Defensive: a link must appear under some section.
        if current_section is None:
            logger.warning(
                f"Line {line_num}: link {url!r} appears before any section "
                "header — skipping"
            )
            skipped_count += 1
            continue

        # Deduplicate by URL — Stripe occasionally lists the same doc twice.
        if url in seen_urls:
            logger.debug(f"Line {line_num}: duplicate URL skipped: {url}")
            skipped_count += 1
            continue
        seen_urls.add(url)

        # Construct and validate the entry. Pydantic does the heavy lifting.
        try:
            entry = LinkEntry(
                url=url,  # type: ignore[arg-type]
                title=title,
                section=current_section,
                description=description,
            )
            entries.append(entry)
        except ValidationError as e:
            logger.warning(
                f"Line {line_num}: validation failed for {url!r}: {e}"
            )
            skipped_count += 1

    logger.info(
        f"Parsed {len(entries)} valid entries; skipped {skipped_count} lines"
    )
    return entries


def validate_collection(collection: LinkCollection) -> None:
    """Run sanity checks on the parsed collection and log a report.

    Args:
        collection: The parsed LinkCollection to inspect.

    Raises:
        ValueError: If critical invariants are violated.
    """
    count = collection.count
    sections = collection.sections

    logger.info("=" * 60)
    logger.info("VALIDATION REPORT")
    logger.info("=" * 60)
    logger.info(f"Total entries: {count:,}")
    logger.info(f"Unique sections: {len(sections)}")

    # Show distribution across sections.
    by_section = collection.entries_by_section()
    for section_name in sorted(by_section.keys()):
        logger.info(f"  • {section_name}: {len(by_section[section_name])} entries")

    # Critical invariants — fail loudly if violated.
    if count < 100:
        raise ValueError(
            f"Expected 400+ entries, got {count}. Parser likely broken."
        )
    if not all(entry.section for entry in collection.entries):
        raise ValueError("Some entries have empty section — parser bug.")

    logger.info("=" * 60)
    logger.info("All invariants passed ✓")
    logger.info("=" * 60)


def save_collection(collection: LinkCollection, output_path: Path) -> None:
    """Save the LinkCollection as pretty-printed JSON.

    Args:
        collection: The validated collection to save.
        output_path: Destination file path.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    json_str = collection.model_dump_json(indent=2)
    output_path.write_text(json_str, encoding="utf-8")

    size_kb = output_path.stat().st_size / 1024
    logger.info(f"Saved collection to {output_path} ({size_kb:.1f} KB)")


def configure_logging() -> None:
    """Configure loguru with both console and file outputs."""
    settings.logs_path.mkdir(parents=True, exist_ok=True)

    logger.remove()  # Remove the default handler
    logger.add(sys.stderr, level=settings.log_level)
    logger.add(
        settings.logs_path / "ingest.log",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


def main() -> int:
    """Entry point. Returns 0 on success, non-zero on failure."""
    configure_logging()
    logger.info("=" * 60)
    logger.info("Stripe Links Fetcher — Stage 1 of ingestion pipeline")
    logger.info("=" * 60)

    try:
        # Step 1: Download.
        text = fetch_llms_txt(str(settings.stripe_llms_url))

        # Step 2: Parse.
        entries = parse_llms_txt(text)

        # Step 3: Wrap in a collection model (adds metadata, validates whole).
        collection = LinkCollection(
            source_url=settings.stripe_llms_url,
            entries=entries,
        )

        # Step 4: Validate invariants.
        validate_collection(collection)

        # Step 5: Save.
        save_collection(collection, OUTPUT_PATH)

        logger.info("✓ Stage 1 complete")
        return 0

    except httpx.HTTPError as e:
        logger.error(f"HTTP error: {e}")
        return 1
    except ValidationError as e:
        logger.error(f"Pydantic validation failed: {e}")
        return 1
    except ValueError as e:
        logger.error(f"Validation invariant failed: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

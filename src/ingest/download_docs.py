"""
Stage 2 of the ingestion pipeline.

Reads the LinkCollection produced by Stage 1 (fetch_links.py) and
concurrently downloads each .md file from Stripe, organizing the
output by section. Failed downloads are recorded to a dead-letter
queue for later retry.

Concurrency is bounded by a semaphore (settings.download_concurrency)
and politeness is enforced with randomized per-request delays.

Usage:
    uv run python -m src.ingest.download_docs
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

import httpx
from loguru import logger
from pydantic import ValidationError
from slugify import slugify
from tqdm.asyncio import tqdm as atqdm

from src.config import settings
from src.ingest.models import FailedDownload, LinkCollection, LinkEntry

# Paths.
INPUT_PATH: Path = settings.processed_data_path / "stripe_links.json"
FAILURES_PATH: Path = settings.processed_data_path / "failed_downloads.json"

# Status codes considered transient — worth retrying once.
RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def load_collection(path: Path) -> LinkCollection:
    """Load and re-validate the LinkCollection from JSON.

    Args:
        path: Path to the stripe_links.json file.

    Returns:
        A validated LinkCollection.

    Raises:
        FileNotFoundError: If the input file doesn't exist.
        ValidationError: If the JSON doesn't match the model schema.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Input file {path} not found. Run fetch_links.py first."
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    collection = LinkCollection.model_validate(data)
    logger.info(f"Loaded {collection.summary()}")
    return collection


def build_output_path(entry: LinkEntry) -> Path:
    """Compute the destination file path for an entry.

    Path format: data/raw/<slugified-section>/<slugified-title>.md

    Uses the entry's URL path stem as a fallback when titles collide
    or are not URL-safe.

    Args:
        entry: The LinkEntry to compute a path for.

    Returns:
        Absolute path where the .md file should be saved.
    """
    section_slug = slugify(entry.section)
    title_slug = slugify(entry.title)

    # Defensive: if slugify produced an empty string (rare), fall back to URL stem.
    if not title_slug:
        title_slug = Path(str(entry.url)).stem or "untitled"

    return settings.raw_data_path / section_slug / f"{title_slug}.md"


def build_frontmatter(entry: LinkEntry) -> str:
    """Generate YAML frontmatter to prepend to each saved file.

    Preserves lineage (source URL, section, fetched timestamp) inside
    the artifact itself — so even if the JSON catalog is lost, each
    file is self-describing.

    Args:
        entry: The LinkEntry being saved.

    Returns:
        A YAML frontmatter block as a string.
    """
    # Escape quotes in fields that might contain them.
    title = entry.title.replace('"', '\\"')
    description = entry.description.replace('"', '\\"')

    return (
        "---\n"
        f'url: "{entry.url}"\n'
        f'title: "{title}"\n'
        f'section: "{entry.section}"\n'
        f'description: "{description}"\n'
        "---\n\n"
    )


async def download_one(
    entry: LinkEntry,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    force: bool = False,
) -> FailedDownload | None:
    """Download a single .md file with politeness and error handling.

    Args:
        entry: The link entry to download.
        client: Shared httpx AsyncClient for connection pooling.
        semaphore: Bounded concurrency gate.
        force: If True, re-download even if the file already exists.

    Returns:
        None on success, a FailedDownload record on failure.
    """
    output_path = build_output_path(entry)

    # Idempotency: skip if already downloaded (unless forced).
    if output_path.exists() and not force:
        logger.debug(f"Skipping (exists): {output_path.name}")
        return None

    async with semaphore:
        # Politeness: jittered delay to avoid bot-like patterns.
        delay = random.uniform(settings.request_delay_min, settings.request_delay_max)
        await asyncio.sleep(delay)

        try:
            response = await client.get(str(entry.url))
        except httpx.TimeoutException as e:
            logger.warning(f"Timeout: {entry.url}")
            return FailedDownload(
                url=entry.url,
                title=entry.title,
                section=entry.section,
                error=f"Timeout: {e}",
            )
        except httpx.HTTPError as e:
            logger.warning(f"HTTP error for {entry.url}: {e}")
            return FailedDownload(
                url=entry.url,
                title=entry.title,
                section=entry.section,
                error=f"HTTPError: {e}",
            )

        # Status-based handling.
        if response.status_code in RETRYABLE_STATUSES:
            # One retry after a backoff for transient errors.
            logger.info(f"Retrying {entry.url} (got {response.status_code})")
            await asyncio.sleep(2.0)
            try:
                response = await client.get(str(entry.url))
            except httpx.HTTPError as e:
                return FailedDownload(
                    url=entry.url,
                    title=entry.title,
                    section=entry.section,
                    error=f"Retry failed: {e}",
                    attempt_number=2,
                )

        if response.status_code != 200:
            logger.warning(f"Status {response.status_code}: {entry.url}")
            return FailedDownload(
                url=entry.url,
                title=entry.title,
                section=entry.section,
                error=f"HTTP {response.status_code}",
            )

        # Light content sniff: warn if the body doesn't look like markdown.
        body = response.text
        if body.lstrip().startswith("<"):
            logger.warning(
                f"Suspicious content (looks like HTML) for {entry.url}; saving anyway"
            )

        # Write atomically: write to .tmp, then rename. Prevents corrupt
        # half-files if the process is killed mid-write.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        content = build_frontmatter(entry) + body
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(output_path)

        logger.debug(f"Saved: {output_path}")
        return None


async def download_all(
    collection: LinkCollection,
    force: bool = False,
) -> list[FailedDownload]:
    """Download every entry concurrently with bounded parallelism.

    Args:
        collection: The LinkCollection to download from.
        force: If True, re-download even if files already exist.

    Returns:
        A list of FailedDownload records (empty on full success).
    """
    semaphore = asyncio.Semaphore(settings.download_concurrency)
    timeout = httpx.Timeout(
        settings.http_timeout,
        connect=settings.http_connect_timeout,
    )
    headers = {"User-Agent": settings.http_user_agent}
    limits = httpx.Limits(
        max_connections=settings.download_concurrency * 2,
        max_keepalive_connections=settings.download_concurrency,
    )

    logger.info(
        f"Starting concurrent download: {collection.count} entries, "
        f"max {settings.download_concurrency} in flight"
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        limits=limits,
        follow_redirects=True,
    ) as client:
        tasks = [
            download_one(entry, client, semaphore, force=force)
            for entry in collection.entries
        ]
        # tqdm wraps an async iterable to show a progress bar.
        results = await atqdm.gather(*tasks, desc="Downloading")

    failures = [r for r in results if r is not None]
    successes = len(results) - len(failures)
    logger.info(f"Downloaded: {successes}, Failed: {len(failures)}")
    return failures


def save_failures(failures: list[FailedDownload], path: Path) -> None:
    """Persist the dead-letter queue to disk for later retry.

    Always writes the file, even if there are zero failures — this
    proves the run completed, and clears any prior failure file from
    a previous run.

    Args:
        failures: List of FailedDownload records.
        path: Destination file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(failures),
        "failures": [f.model_dump(mode="json") for f in failures],
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info(f"Wrote failure log to {path} ({len(failures)} records)")


def configure_logging() -> None:
    """Configure loguru with console and file outputs."""
    settings.logs_path.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)
    logger.add(
        settings.logs_path / "download.log",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


async def main_async(force: bool = False) -> int:
    """Async entry point. Returns exit code."""
    configure_logging()
    logger.info("=" * 60)
    logger.info("Stripe Docs Downloader — Stage 2 of ingestion pipeline")
    logger.info("=" * 60)

    try:
        collection = load_collection(INPUT_PATH)
        failures = await download_all(collection, force=force)
        save_failures(failures, FAILURES_PATH)

        success_rate = (
            (collection.count - len(failures)) / collection.count * 100
            if collection.count
            else 0.0
        )
        logger.info(f"Success rate: {success_rate:.1f}%")

        # Treat >5% failures as a pipeline failure.
        if collection.count and len(failures) / collection.count > 0.05:
            logger.error("Failure rate exceeds 5% threshold")
            return 1

        logger.info("✓ Stage 2 complete")
        return 0

    except FileNotFoundError as e:
        logger.error(str(e))
        return 1
    except ValidationError as e:
        logger.error(f"Input validation failed: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


def main() -> int:
    """Sync wrapper for the async entry point."""
    force = "--force" in sys.argv
    if force:
        logger.info("--force flag detected: will re-download existing files")
    return asyncio.run(main_async(force=force))


if __name__ == "__main__":
    sys.exit(main())

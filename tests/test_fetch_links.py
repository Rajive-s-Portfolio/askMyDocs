"""
Unit tests for the fetch_links module.

These tests exercise pure parsing logic against synthetic markdown
samples — no network calls, no file system access (except via tmp_path).
This keeps tests fast (<1s total), reliable, and CI-friendly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.ingest.download_docs import build_output_path
from src.ingest.fetch_links import (
    parse_llms_txt,
    save_collection,
    validate_collection,
)

from src.ingest.models import LinkCollection, LinkEntry



# ============================================================
# Fixtures — reusable test data
# ============================================================


@pytest.fixture
def minimal_sample() -> str:
    """A minimal valid sample with one section and one link."""
    return (
        "# Stripe Docs\n"
        "\n"
        "## Payments\n"
        "- [Charges](https://docs.stripe.com/api/charges.md): "
        "Use the Charges API to process payments.\n"
    )


@pytest.fixture
def multi_section_sample() -> str:
    """A sample with multiple sections and multiple links per section."""
    return (
        "# Stripe Docs\n"
        "\n"
        "## Payments\n"
        "- [Charges](https://docs.stripe.com/api/charges.md): Charges API.\n"
        "- [Payment Intents](https://docs.stripe.com/api/payment_intents.md): "
        "Payment Intents API.\n"
        "\n"
        "## Checkout\n"
        "- [Sessions](https://docs.stripe.com/api/checkout/sessions.md): "
        "Checkout sessions.\n"
        "\n"
        "## Billing\n"
        "- [Subscriptions](https://docs.stripe.com/api/subscriptions.md): "
        "Subscriptions API.\n"
        "- [Invoices](https://docs.stripe.com/api/invoices.md): Invoices API.\n"
    )


# ============================================================
# Category A: Happy Path
# ============================================================


class TestParserHappyPath:
    """Verify the parser correctly handles well-formed input."""

    def test_minimal_sample_returns_one_entry(self, minimal_sample: str) -> None:
        entries = parse_llms_txt(minimal_sample)
        assert len(entries) == 1

    def test_minimal_sample_extracts_correct_fields(
        self, minimal_sample: str
    ) -> None:
        entries = parse_llms_txt(minimal_sample)
        entry = entries[0]
        assert entry.title == "Charges"
        assert str(entry.url) == "https://docs.stripe.com/api/charges.md"
        assert entry.section == "Payments"
        assert "Charges API" in entry.description

    def test_multi_section_total_count(self, multi_section_sample: str) -> None:
        entries = parse_llms_txt(multi_section_sample)
        assert len(entries) == 5

    def test_multi_section_assigns_correct_sections(
        self, multi_section_sample: str
    ) -> None:
        entries = parse_llms_txt(multi_section_sample)
        sections_seen = {e.section for e in entries}
        assert sections_seen == {"Payments", "Checkout", "Billing"}

    def test_section_grouping_is_correct(self, multi_section_sample: str) -> None:
        entries = parse_llms_txt(multi_section_sample)
        by_section: dict[str, list[LinkEntry]] = {}
        for e in entries:
            by_section.setdefault(e.section, []).append(e)

        assert len(by_section["Payments"]) == 2
        assert len(by_section["Checkout"]) == 1
        assert len(by_section["Billing"]) == 2


# ============================================================
# Category B: Edge Cases
# ============================================================


class TestParserEdgeCases:
    """Verify the parser handles unusual but valid input."""

    def test_empty_string_returns_empty_list(self) -> None:
        assert parse_llms_txt("") == []

    def test_only_blank_lines_returns_empty_list(self) -> None:
        assert parse_llms_txt("\n\n\n\n") == []

    def test_only_top_level_header_returns_empty(self) -> None:
        assert parse_llms_txt("# Just a Title\n\nSome prose.\n") == []

    def test_sections_without_links_return_empty(self) -> None:
        text = "## Payments\n\n## Checkout\n\n## Billing\n"
        assert parse_llms_txt(text) == []

    def test_link_without_description_works(self) -> None:
        text = (
            "## Payments\n"
            "- [Foo](https://docs.stripe.com/foo.md)\n"
        )
        entries = parse_llms_txt(text)
        assert len(entries) == 1
        assert entries[0].description == ""

    def test_section_with_ampersand(self) -> None:
        text = (
            "## Issuing & Cards\n"
            "- [Foo](https://docs.stripe.com/foo.md): Bar.\n"
        )
        entries = parse_llms_txt(text)
        assert entries[0].section == "Issuing & Cards"

    def test_extra_blank_lines_between_entries(self) -> None:
        text = (
            "## Payments\n"
            "\n"
            "\n"
            "- [Foo](https://docs.stripe.com/foo.md): A.\n"
            "\n"
            "\n"
            "- [Bar](https://docs.stripe.com/bar.md): B.\n"
        )
        entries = parse_llms_txt(text)
        assert len(entries) == 2


# ============================================================
# Category C: Filtering Logic
# ============================================================


class TestParserFiltering:
    """Verify the parser correctly filters or rejects bad input."""

    def test_non_markdown_urls_are_filtered(self) -> None:
        text = (
            "## Payments\n"
            "- [HTML page](https://docs.stripe.com/foo.html): An HTML page.\n"
            "- [Markdown page](https://docs.stripe.com/bar.md): A markdown page.\n"
            "- [PDF page](https://docs.stripe.com/baz.pdf): A PDF.\n"
        )
        entries = parse_llms_txt(text)
        assert len(entries) == 1
        assert str(entries[0].url).endswith(".md")

    def test_duplicate_urls_are_deduplicated(self) -> None:
        text = (
            "## Payments\n"
            "- [Foo](https://docs.stripe.com/foo.md): First.\n"
            "- [Foo Again](https://docs.stripe.com/foo.md): Duplicate.\n"
            "- [Bar](https://docs.stripe.com/bar.md): Different.\n"
        )
        entries = parse_llms_txt(text)
        assert len(entries) == 2
        urls = [str(e.url) for e in entries]
        assert urls.count("https://docs.stripe.com/foo.md") == 1

    def test_links_before_any_section_are_skipped(self) -> None:
        text = (
            "- [Orphan](https://docs.stripe.com/orphan.md): No section.\n"
            "## Payments\n"
            "- [Charges](https://docs.stripe.com/charges.md): Has section.\n"
        )
        entries = parse_llms_txt(text)
        assert len(entries) == 1
        assert entries[0].title == "Charges"


# ============================================================
# Category D: Path & Filename Logic
# ============================================================


class TestPathBuilding:
    """Verify file path construction handles odd inputs safely."""

    @pytest.mark.parametrize(
        "section,title,expected_section_slug,expected_title_slug",
        [
            ("Payments", "Charges API", "payments", "charges-api"),
            ("Issuing & Cards", "Card Creation", "issuing-and-cards",
             "card-creation"),
            ("Connect", "OAuth", "connect", "oauth"),
            ("Billing", "Invoices & Receipts", "billing",
             "invoices-and-receipts"),
        ],
    )
    def test_slugify_produces_expected_paths(
        self,
        section: str,
        title: str,
        expected_section_slug: str,
        expected_title_slug: str,
    ) -> None:
        entry = LinkEntry(
            url="https://docs.stripe.com/api/x.md",  # type: ignore[arg-type]
            title=title,
            section=section,
        )
        path = build_output_path(entry)

        # Path should end with <section_slug>/<title_slug>.md
        assert path.parent.name == expected_section_slug
        assert path.name == f"{expected_title_slug}.md"

    def test_path_is_under_raw_data_dir(self) -> None:
        entry = LinkEntry(
            url="https://docs.stripe.com/api/x.md",  # type: ignore[arg-type]
            title="Foo",
            section="Payments",
        )
        path = build_output_path(entry)
        # Path must be inside data/raw/
        assert "raw" in path.parts


# ============================================================
# Category E: Save / Load Round-Trip
# ============================================================


class TestSaveAndLoad:
    """Verify saved JSON round-trips back to a valid LinkCollection."""

    def test_save_and_reload_round_trip(self, tmp_path: Path) -> None:
        # Build a small collection.
        entries = [
            LinkEntry(
                url="https://docs.stripe.com/api/charges.md",  # type: ignore[arg-type]
                title="Charges",
                section="Payments",
                description="Process charges.",
            ),
            LinkEntry(
                url="https://docs.stripe.com/api/refunds.md",  # type: ignore[arg-type]
                title="Refunds",
                section="Payments",
                description="Issue refunds.",
            ),
        ]
        original = LinkCollection(
            source_url="https://docs.stripe.com/llms.txt",  # type: ignore[arg-type]
            entries=entries,
        )

        # Save it.
        output_path = tmp_path / "collection.json"
        save_collection(original, output_path)

        # Confirm file exists and is non-empty.
        assert output_path.exists()
        assert output_path.stat().st_size > 0

        # Reload and re-validate.
        raw = json.loads(output_path.read_text(encoding="utf-8"))
        reloaded = LinkCollection.model_validate(raw)

        # Check structural equality.
        assert reloaded.count == original.count
        assert reloaded.sections == original.sections
        assert reloaded.entries[0].title == "Charges"
        assert reloaded.entries[1].title == "Refunds"


# ============================================================
# Category F: Invariant Validation
# ============================================================


class TestValidateCollection:
    """Verify validate_collection enforces invariants."""

    def test_raises_on_too_few_entries(self) -> None:
        # Build a tiny collection — should trip the < 100 invariant.
        entries = [
            LinkEntry(
                url="https://docs.stripe.com/api/x.md",  # type: ignore[arg-type]
                title="X",
                section="Payments",
            ),
        ]
        collection = LinkCollection(
            source_url="https://docs.stripe.com/llms.txt",  # type: ignore[arg-type]
            entries=entries,
        )
        with pytest.raises(ValueError, match="Expected 400"):
            validate_collection(collection)


# ============================================================
# Category G: Model-Level Validation (defensive)
# ============================================================


class TestLinkEntryModel:
    """Verify the LinkEntry model itself enforces its contract.

    These overlap with model unit tests but are valuable here as
    integration-style guards.
    """

    def test_rejects_non_markdown_url(self) -> None:
        with pytest.raises(ValidationError):
            LinkEntry(
                url="https://docs.stripe.com/foo.html",  # type: ignore[arg-type]
                title="Foo",
                section="Payments",
            )

    def test_rejects_empty_section(self) -> None:
        with pytest.raises(ValidationError):
            LinkEntry(
                url="https://docs.stripe.com/foo.md",  # type: ignore[arg-type]
                title="Foo",
                section="",
            )

    def test_rejects_empty_title(self) -> None:
        with pytest.raises(ValidationError):
            LinkEntry(
                url="https://docs.stripe.com/foo.md",  # type: ignore[arg-type]
                title="",
                section="Payments",
            )

    def test_is_frozen(self) -> None:
        entry = LinkEntry(
            url="https://docs.stripe.com/foo.md",  # type: ignore[arg-type]
            title="Foo",
            section="Payments",
        )
        with pytest.raises(ValidationError):
            entry.title = "Bar"  # type: ignore[misc]
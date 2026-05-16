"""
Pydantic data models for the ingestion pipeline.

These models define the shape of data flowing through the pipeline:
- `LinkEntry`: a single parsed link from Stripe's llms.txt index
- `LinkCollection`: a validated collection of LinkEntry objects with metadata
- `FailedDownload`: a record of a failed download attempt (dead-letter queue)

All models enforce strict validation. Constructing an invalid instance
raises a ValidationError immediately, surfacing bugs near their source
rather than letting bad data propagate through the system.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
)

# Reusable type aliases for constrained strings.
# Using Annotated makes the constraints visible in IDE hovers and docs.
NonEmptyStr = Annotated[
    str,
    StringConstraints(min_length=1, strip_whitespace=True),
]


class LinkEntry(BaseModel):
    """A single link entry parsed from Stripe's llms.txt index."""

    model_config = ConfigDict(
        # Reject extra fields not declared in the model — surfaces bugs early.
        extra="forbid",
        # Strip whitespace from all string fields automatically.
        str_strip_whitespace=True,
        # Validate on assignment, not just on construction.
        validate_assignment=True,
        # Freeze the model after creation — immutable by default.
        frozen=True,
    )

    url: HttpUrl = Field(
        ...,
        description="Absolute URL to the markdown document.",
    )
    title: NonEmptyStr = Field(
        ...,
        description="Human-readable title of the document.",
    )
    section: NonEmptyStr = Field(
        ...,
        description="Section header the link sits under (e.g., 'Payments').",
    )
    description: str = Field(
        default="",
        description="Short description of the document; may be empty.",
    )

    @field_validator("url")
    @classmethod
    def validate_is_markdown_url(cls, v: HttpUrl) -> HttpUrl:
        """Ensure the URL ends with .md (we only ingest markdown sources)."""
        if not str(v).endswith(".md"):
            raise ValueError(f"URL must end with .md, got: {v}")
        return v


class LinkCollection(BaseModel):
    """A validated collection of LinkEntry objects with pipeline metadata."""

    model_config = ConfigDict(extra="forbid")

    source_url: HttpUrl = Field(
        ...,
        description="The llms.txt URL these entries were parsed from.",
    )
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the source was fetched.",
    )
    entries: list[LinkEntry] = Field(
        default_factory=list,
        description="The parsed link entries.",
    )

    @property
    def count(self) -> int:
        """Total number of entries in the collection."""
        return len(self.entries)

    @property
    def sections(self) -> set[str]:
        """Unique section names across all entries."""
        return {entry.section for entry in self.entries}

    def entries_by_section(self) -> dict[str, list[LinkEntry]]:
        """Group entries by section name."""
        grouped: dict[str, list[LinkEntry]] = {}
        for entry in self.entries:
            grouped.setdefault(entry.section, []).append(entry)
        return grouped

    def summary(self) -> str:
        """Human-readable summary for logging and inspection."""
        return (
            f"LinkCollection: {self.count} entries across "
            f"{len(self.sections)} sections, fetched at "
            f"{self.fetched_at.isoformat()}"
        )


class FailedDownload(BaseModel):
    """A record of a failed download attempt for the dead-letter queue."""

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl = Field(..., description="The URL that failed to download.")
    title: NonEmptyStr = Field(..., description="Title of the failed document.")
    section: NonEmptyStr = Field(..., description="Section of the failed document.")
    error: NonEmptyStr = Field(..., description="Error message or status code.")
    attempted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the failed attempt.",
    )
    attempt_number: int = Field(
        default=1,
        ge=1,
        description="Which attempt this was (1 = first attempt).",
    )

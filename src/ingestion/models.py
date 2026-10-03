"""Canonical document model.

Every loader returns a ``Document`` and every downstream stage (chunking,
embedding, indexing) consumes one. Owning this type means the pipeline never
depends on a framework's own ``Document`` class.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, kw_only=True)
class Document:
    """One loaded document in canonical form.

    Attributes:
        id: Stable identifier for this document. Deterministic for a given
            source so that re-ingesting the same file yields the same id.
        source: Where the document came from, as a URI-like string (a resolved
            file path today, possibly ``s3://...`` or ``https://...`` later).
        source_type: Kind of source, e.g. ``"txt"`` or ``"pdf"``.
        content: The extracted text.
        metadata: Free-form details about the document. Keep values
            JSON-serializable (str, int, float, bool, None, list, dict) because
            this ends up as the payload stored next to vectors.

    Instances are immutable. To get a modified copy use
    ``dataclasses.replace(doc, content=...)``.
    """

    id: str
    source: str
    source_type: str
    content: str = field(repr=False)  # can be megabytes; keep it out of logs and print()
    metadata: dict[str, Any] = field(default_factory=dict, hash=False)  # dicts are unhashable

    def __post_init__(self) -> None:
        # A Document must be impossible to construct in an invalid state.
        for name in ("id", "source", "source_type"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"Document.{name} must be str, got {type(value).__name__}")
            if not value.strip():
                raise ValueError(f"Document.{name} must not be empty")
        if not isinstance(self.content, str):
            raise TypeError(f"Document.content must be str, got {type(self.content).__name__}")
        if not isinstance(self.metadata, dict):
            raise TypeError(f"Document.metadata must be dict, got {type(self.metadata).__name__}")

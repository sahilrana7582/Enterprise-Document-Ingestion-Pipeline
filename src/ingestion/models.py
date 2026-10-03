"""Canonical document model.

Every loader returns a ``Document`` and every downstream stage (chunking,
embedding, indexing) consumes one. Owning this type means the core pipeline
never depends on a framework's own ``Document`` class; the LangChain bridge
lives in ``src.ingestion.adapters.langchain``.
"""

from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


NonBlankStr = Annotated[str, AfterValidator(_reject_blank)]
"""A ``str`` that is not empty or whitespace-only. The value itself is left untouched."""


class FrozenModel(BaseModel):
    """Base for every data model in the project, so the policy lives in one place.

    * ``frozen``: instances cannot be reassigned after creation.
    * ``extra="forbid"``: an unknown or typo'd field name fails loudly.
    * ``strict``: wrong types are rejected rather than coerced (``bytes`` is never
      silently decoded into a ``str``, ``"10"`` never becomes ``10``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Document(FrozenModel):
    """One loaded document in canonical form.

    ``metadata`` must be JSON-serializable because it ends up as the payload
    stored next to vectors.

    To get a modified copy use :meth:`replace`, not ``model_copy(update=...)``:
    ``model_copy`` skips validation and would happily produce a Document with an
    empty ``id``.
    """

    id: NonBlankStr = Field(
        description="Stable identifier. Deterministic for a given source, so re-ingesting "
        "the same file yields the same id."
    )
    checksum: NonBlankStr = Field(description="To identify the duplicate files")
    source: NonBlankStr = Field(
        description="Where the document came from, as a URI-like string (a resolved file "
        "path today, possibly s3://... or https://... later)."
    )
    source_type: NonBlankStr = Field(description='Kind of source, e.g. "txt" or "pdf".')
    content: str = Field(
        repr=False,  # can be megabytes; keep it out of logs and print()
        description="The extracted text. May be empty: whether that is acceptable is a "
        "loader/validation policy, not a property of the model.",
    )
    metadata: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Free-form, JSON-serializable details about the document.",
    )

    def __hash__(self) -> int:
        # The default frozen-model hash covers every field, and a dict field makes
        # that raise ``TypeError: unhashable type: 'dict'``. Equal documents still
        # hash equally, because metadata is only left out of the hash, not of ``==``.
        return hash((self.id, self.source, self.source_type, self.content))

    def replace(self, **changes: Any) -> "Document":
        """Return a copy with ``changes`` applied, validated like any new Document."""
        return type(self)(**{**dict(self), **changes})


class FailureRecord(FrozenModel):
    source: NonBlankStr
    error_type: NonBlankStr
    message: str
class DuplicateRecord(FrozenModel):
    source: NonBlankStr
    duplicate_of: NonBlankStr
class IngestionResult(FrozenModel):
    discovered: int = Field(ge=0)
    documents: list[Document] = Field(default_factory=list)
    failures: list[FailureRecord] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list) 
    duplicates: list[DuplicateRecord] = Field(default_factory=list)
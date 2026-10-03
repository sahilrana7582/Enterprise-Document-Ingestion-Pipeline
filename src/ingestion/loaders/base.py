"""The contract every loader implements."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from pydantic import Field, PositiveInt

from src.ingestion.models import Document, FrozenModel


class LoaderConfig(FrozenModel):
    """Settings shared by every loader. Immutable, validated on construction."""

    max_bytes: PositiveInt = Field(
        default=50 * 1024 * 1024,
        description="Files larger than this are refused before they are read.",
    )


class BaseLoader(ABC):
    """Turns one file into one canonical ``Document``.

    Subclasses set two class attributes and implement ``load``:

    * ``source_type``: label stored on every Document they produce (``"txt"``).
    * ``supported_extensions``: lowercase extensions with the dot (``(".txt",)``).
      The loader registry will build its extension -> loader map from this, so
      nothing about file types is hard-coded anywhere else.

    ``load`` must raise a ``DocumentLoadError`` (or a subclass) for any problem
    caused by the file, so the pipeline can isolate per-file failures.

    Configuration arrives as a ``LoaderConfig`` (see ``self.config``), so a bad
    value fails at construction, not halfway through an ingestion run.
    """

    source_type: ClassVar[str]
    supported_extensions: ClassVar[tuple[str, ...]]

    def __init__(self, config: LoaderConfig | None = None) -> None:
        self.config = LoaderConfig() if config is None else config

    @abstractmethod
    def load(self, path: str | Path) -> Document:
        """Read ``path`` and return it as a ``Document``."""

    def supports(self, path: str | Path) -> bool:
        """True if ``path`` has an extension this loader handles (case-insensitive)."""
        return Path(path).suffix.lower() in self.supported_extensions

"""The contract every loader implements."""

import hashlib
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISREG
from typing import Any, ClassVar, NamedTuple

from pydantic import Field, PositiveInt

from src.ingestion.exceptions import (
    DocumentReadError,
    EmptyDocumentError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from src.ingestion.models import Document, FrozenModel


class Extracted(NamedTuple):
    content: str
    metadata: dict[str, Any]


class LoaderConfig(FrozenModel):
    """Settings shared by every loader. Immutable, validated on construction."""

    max_bytes: PositiveInt = Field(
        default=50 * 1024 * 1024,
        description="Files larger than this are refused before they are read.",
    )


class BaseLoader(ABC):
    """Turns one file into one canonical ``Document``.

    ``load`` does everything every format needs: extension check, size limit,
    reading the file, empty check, id and common metadata. A subclass does not
    override ``load``. It only sets two class attributes and implements ``_extract``:

    * ``source_type``: label stored on every Document it produces (``"txt"``).
    * ``supported_extensions``: lowercase extensions with the dot (``(".txt",)``).
    * ``_extract``: turn the file's raw bytes into text, plus any metadata that
      only that format knows (``{"encoding": ...}`` for text, ``{"page_count": ...}``
      for PDF).

    Every problem caused by the file must be raised as a ``DocumentLoadError``
    (or a subclass), so the pipeline can skip that file and keep going.
    """

    source_type: ClassVar[str]
    supported_extensions: ClassVar[tuple[str, ...]]

    def __init__(self, config: LoaderConfig | None = None) -> None:
        self.config = LoaderConfig() if config is None else config

    def load(self, path: str | Path) -> Document:
        """Read ``path`` and return it as a ``Document``."""

        requested = Path(path)

        if not self.supports(requested):
            raise UnsupportedFileTypeError(
                str(requested),
                f"extension {requested.suffix!r} is not supported "
                f"(expected one of {', '.join(self.supported_extensions)})",
            )

        resolved = _resolve(requested)
        source = str(resolved)

        file_stat = _stat_regular_file(resolved, source)

        max_bytes = self.config.max_bytes
        if file_stat.st_size > max_bytes:
            raise FileTooLargeError(
                source,
                f"{file_stat.st_size} bytes exceeds the limit of {max_bytes}",
            )

        raw = _read_bytes(resolved, source)

        # Template Method:
        # BaseLoader controls the workflow.
        # Concrete loaders implement _extract().
        extracted = self._extract(raw, resolved)

        if not extracted.content.strip():
            raise EmptyDocumentError(
                source,
                "file contains no text",
            )

        metadata = _build_metadata(
            resolved,
            file_stat,
            content=extracted.content,
        )

        metadata.update(extracted.metadata)

        return Document(
            id=_document_id(source),
            source=source,
            source_type=self.source_type,
            content=extracted.content,
            metadata=metadata,
        )

    @abstractmethod
    def _extract(self, raw: bytes, path: Path) -> Extracted:
        """Extract text and loader-specific metadata from raw file bytes."""
        ...

    def supports(self, path: str | Path) -> bool:
        """True if ``path`` has an extension this loader handles."""
        return Path(path).suffix.lower() in self.supported_extensions


def _resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise DocumentReadError(
            str(path),
            f"cannot resolve path: {exc}",
        ) from exc


def _describe_os_error(exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError):
        return "file not found"

    if isinstance(exc, PermissionError):
        return "permission denied"

    return exc.strerror or str(exc)


def _stat_regular_file(path: Path, source: str) -> os.stat_result:
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise DocumentReadError(
            source,
            _describe_os_error(exc),
        ) from exc

    if not S_ISREG(file_stat.st_mode):
        raise DocumentReadError(
            source,
            "not a regular file (is it a directory?)",
        )

    return file_stat


def _read_bytes(path: Path, source: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DocumentReadError(
            source,
            _describe_os_error(exc),
        ) from exc


def _document_id(source: str) -> str:
    """Create a deterministic ID from the resolved source path."""

    digest = hashlib.sha256(
        source.encode("utf-8", errors="surrogateescape")
    )

    return digest.hexdigest()[:16]


def _build_metadata(
    path: Path,
    file_stat: os.stat_result,
    content: str,
) -> dict[str, Any]:
    """Build JSON-serializable metadata common to every loader."""

    return {
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": file_stat.st_size,
        "modified_at": datetime.fromtimestamp(
            file_stat.st_mtime,
            tz=timezone.utc,
        ).isoformat(),
        "char_count": len(content),
    }
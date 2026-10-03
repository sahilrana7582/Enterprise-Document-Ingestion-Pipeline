"""Loader for plain-text (``.txt``) files."""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISREG
from typing import Any, ClassVar

from src.ingestion.exceptions import (
    DocumentDecodeError,
    DocumentReadError,
    EmptyDocumentError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from src.ingestion.loaders.base import BaseLoader
from src.ingestion.models import Document

# (codec to try, label recorded in metadata), in order of preference.
# utf-8-sig decodes UTF-8 *and* strips the BOM that Windows Notepad adds.
# cp1252 is the fallback for legacy Windows files. Not latin-1: latin-1 can
# decode any byte sequence, so it would hide corruption instead of reporting it.
_ENCODINGS = (("utf-8-sig", "utf-8"), ("cp1252", "cp1252"))


class TextLoader(BaseLoader):
    """Loads ``.txt`` files, tolerating the usual real-world mess.

    Handles UTF-8 (with or without BOM), cp1252, and any newline convention
    (``\\n``, ``\\r\\n``, ``\\r``), which are normalized to ``\\n``. Rejects missing
    or oversized files, binary data, and files with no text.
    """

    source_type: ClassVar[str] = "txt"
    supported_extensions: ClassVar[tuple[str, ...]] = (".txt",)

    def load(self, path: str | Path) -> Document:
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
                source, f"{file_stat.st_size} bytes exceeds the limit of {max_bytes}"
            )

        raw = _read_bytes(resolved, source)
        if b"\x00" in raw:
            raise DocumentDecodeError(
                source, "contains NUL bytes; looks binary (UTF-16/32 text is not supported)"
            )
        text, encoding = _decode(raw, source)
        content = _normalize_newlines(text)
        if not content.strip():
            raise EmptyDocumentError(source, "file contains no text")

        return Document(
            id=_document_id(source),
            source=source,
            source_type=self.source_type,
            content=content,
            metadata=_build_metadata(resolved, file_stat, encoding, content),
        )


def _resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError) as exc:  # RuntimeError: symlink loop, unknown ~user
        raise DocumentReadError(str(path), f"cannot resolve path: {exc}") from exc


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
        raise DocumentReadError(source, _describe_os_error(exc)) from exc
    if not S_ISREG(file_stat.st_mode):
        raise DocumentReadError(source, "not a regular file (is it a directory?)")
    return file_stat


def _read_bytes(path: Path, source: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DocumentReadError(source, _describe_os_error(exc)) from exc


def _decode(raw: bytes, source: str) -> tuple[str, str]:
    """Return ``(text, encoding_label)`` using the first codec that succeeds."""
    for codec, label in _ENCODINGS:
        try:
            return raw.decode(codec), label
        except UnicodeDecodeError:
            continue
    raise DocumentDecodeError(source, "not valid UTF-8 or cp1252 text")


def _normalize_newlines(text: str) -> str:
    # Order matters: handle "\r\n" first, or each Windows newline becomes two "\n".
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _document_id(source: str) -> str:
    # Deterministic (same path -> same id) so re-ingesting a file never creates
    # a duplicate. 64 bits is ample for millions of documents.
    digest = hashlib.sha256(source.encode("utf-8", errors="surrogateescape"))
    return digest.hexdigest()[:16]


def _build_metadata(
    path: Path, file_stat: os.stat_result, encoding: str, content: str
) -> dict[str, Any]:
    # JSON-serializable values only: this becomes the payload stored with vectors.
    return {
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": file_stat.st_size,
        "encoding": encoding,
        "modified_at": datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc).isoformat(),
        "char_count": len(content),
    }

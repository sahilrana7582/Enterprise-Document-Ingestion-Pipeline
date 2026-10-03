"""Loader for plain-text (``.txt``) files."""

from pathlib import Path
from typing import ClassVar

from src.ingestion.exceptions import DocumentDecodeError
from src.ingestion.loaders.base import BaseLoader, Extracted


# Codec to try, label recorded in metadata, in order of preference.
# utf-8-sig decodes UTF-8 and strips the BOM that Windows Notepad adds.
# cp1252 is the fallback for legacy Windows files.
# latin-1 is intentionally not used because it can decode any byte sequence
# and would therefore hide corruption.
_ENCODINGS = (
    ("utf-8-sig", "utf-8"),
    ("cp1252", "cp1252"),
)


class TextLoader(BaseLoader):
    """Loads ``.txt`` files."""

    source_type: ClassVar[str] = "txt"
    supported_extensions: ClassVar[tuple[str, ...]] = (".txt",)

    def _extract(self, raw: bytes, path: Path) -> Extracted:
        source = str(path)

        # Plain text never contains NUL bytes. PDF and DOCX files legitimately do,
        # which is why this check lives here and not in BaseLoader.
        if b"\x00" in raw:
            raise DocumentDecodeError(
                source,
                "contains NUL bytes; looks binary (UTF-16/32 text is not supported)",
            )

        text, encoding = _decode(raw, source)

        return Extracted(
            content=_normalize_newlines(text),
            metadata={
                "encoding": encoding,
            },
        )


def _decode(raw: bytes, source: str) -> tuple[str, str]:
    """Return ``(text, encoding_label)`` using the first codec that succeeds."""

    for codec, label in _ENCODINGS:
        try:
            return raw.decode(codec), label
        except UnicodeDecodeError:
            continue

    raise DocumentDecodeError(
        source,
        "not valid UTF-8 or cp1252 text",
    )


def _normalize_newlines(text: str) -> str:
    """Normalize all common newline conventions to ``\\n``."""

    return text.replace("\r\n", "\n").replace("\r", "\n")
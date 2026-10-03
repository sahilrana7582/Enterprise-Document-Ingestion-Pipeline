from collections.abc import Sequence

from src.ingestion.models import Document
from src.ingestion.chunking.base import ChunkerConfig, Piece, Chunker


class RecursiveChunker(Chunker):
    def __init__(
        self,
        config: ChunkerConfig | None = None,
        separators: tuple[str, ...] = ("\n\n", "\n", ". ", " "),
    ) -> None:
        self.config = config or ChunkerConfig()
        self.separators = separators

    def split_text(
        self,
        text: str,
        separators: Sequence[str] | None = None,
    ) -> list[str]:
        separators = self.separators if separators is None else separators

        if self._length(text) <= self.config.chunk_size:
            return [text]

        if not separators:
            return [
                text[i : i + self.config.chunk_size]
                for i in range(0, len(text), self.config.chunk_size)
            ]

        sep = separators[0]

        if sep not in text:
            return self.split_text(text, separators[1:])

        pieces = self._split_keep_separator(text, sep)

        small: list[str] = []
        result: list[str] = []

        for piece in pieces:
            if self._length(piece) > self.config.chunk_size:
                if small:
                    result.extend(self._merge(small))
                    small = []

                result.extend(
                    self.split_text(piece, separators[1:])
                )
            else:
                small.append(piece)

        if small:
            result.extend(self._merge(small))

        return result

    def _split(self, document: Document) -> list[Piece]:
        return [
            Piece(text=text, metadata={})
            for text in self.split_text(document.content)
        ]

    @staticmethod
    def _split_keep_separator(text: str, sep: str) -> list[str]:
        parts = text.split(sep)

        result: list[str] = []

        for index, part in enumerate(parts):
            if index < len(parts) - 1:
                result.append(part + sep)
            elif part:
                result.append(part)

        return result

    def _merge(self, pieces: list[str]) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []

        for piece in pieces:
            if (
                current
                and self._length("".join(current + [piece]))
                > self.config.chunk_size
            ):
                chunks.append("".join(current))

                while current and (
                    self._length("".join(current))
                    > self.config.chunk_overlap
                    or self._length("".join(current + [piece]))
                    > self.config.chunk_size
                ):
                    current.pop(0)

            current.append(piece)

        if current:
            chunks.append("".join(current))

        return chunks

    @staticmethod
    def _length(text: str) -> int:
        return len(text)
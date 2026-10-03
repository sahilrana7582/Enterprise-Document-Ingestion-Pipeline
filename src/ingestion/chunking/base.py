from abc import ABC, abstractmethod
from src.ingestion.chunking.model import (
    ChunkerConfig,
    Piece,
    Chunk
)
from src.ingestion.models import (
    Document
)

class Chunker(ABC):

    def __init__(self, chunker_config: ChunkerConfig | None = None):
        if chunker_config is None:
            self.chunker_config = ChunkerConfig()
        else:
            self.chunker_config = chunker_config

    def chunk(self, document: Document) -> list[Chunk]:
        pieces = self._split(document)

        chunks: list[Chunk] = []
        cursor = 0
        index = 0

        for piece in pieces:
            text = piece.text.strip()

            if not text:
                continue

            start = document.content.find(text, cursor)

            if start == -1:
                raise ValueError(
                    f"Chunker returned text that was not found in the document "
                    f"for document {document.id!r}"
                )

            cursor = start + 1

            chunk = Chunk(
                id=f"{document.id}-{index}",
                document_id=document.id,
                index=index,
                content=text,
                start=start,
                end=start + len(text),
                metadata={
                    **document.metadata,
                    "source": document.source,
                    "source_type": document.source_type,
                    **piece.metadata,
                },
            )

            chunks.append(chunk)
            index += 1

        return chunks

    def _length(self, text: str) -> int:
        return len(text)

    @abstractmethod
    def _split(self, document: Document) -> list[Piece]:
        """Split the document into pieces of text, each with optional extra metadata."""

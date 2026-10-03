from typing import Any, NamedTuple

from pydantic import Field, JsonValue

from src.ingestion.models import FrozenModel, NonBlankStr

class Chunk(FrozenModel):
    id: NonBlankStr
    document_id: NonBlankStr
    index: int = Field(ge=0)
    content: NonBlankStr = Field(repr=False)     # never log text
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

class ChunkerConfig(FrozenModel):
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)

class Piece(NamedTuple):
    text: str
    metadata: dict[str, Any]

class DocumentProfile(FrozenModel):
    char_count: int
    paragraph_count: int
    max_paragraph_chars: int
    heading_count: int
    table_line_count: int
    list_item_count: int
    is_structured: bool
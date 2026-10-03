"""Converts canonical documents into LangChain documents.

The core pipeline stays free of LangChain; this is the single place that knows
about it. Use it where a LangChain component consumes our output, for example
``RecursiveCharacterTextSplitter.split_documents`` or a LangChain vector store.
"""

from collections.abc import Iterable

from langchain_core.documents import Document as LangChainDocument

from src.ingestion.models import Document


def to_langchain(document: Document) -> LangChainDocument:
    """Convert one ``Document``.

    ``source`` and ``source_type`` are copied into the LangChain metadata (LangChain
    components conventionally read ``metadata["source"]`` for citations). If
    ``document.metadata`` already has either key, our canonical value wins.
    """
    return LangChainDocument(
        id=document.id,
        page_content=document.content,
        metadata={
            **document.metadata,
            "source": document.source,
            "source_type": document.source_type,
        },
    )


def to_langchain_many(documents: Iterable[Document]) -> list[LangChainDocument]:
    """Convert several ``Document`` objects, preserving order."""
    return [to_langchain(document) for document in documents]

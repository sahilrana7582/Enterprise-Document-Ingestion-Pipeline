import re

from src.ingestion.models import Document, DocumentProfile


class DocumentAnalyzer:
    def analyze(self, document: Document) -> DocumentProfile:
        text = document.content

        paragraphs = self._paragraphs(text)

        char_count = len(text)
        paragraph_count = len(paragraphs)

        max_paragraph_chars = max(
            (len(paragraph) for paragraph in paragraphs),
            default=0,
        )

        heading_count = self._count_headings(text)
        table_line_count = self._count_table_lines(text)
        list_item_count = self._count_list_items(text)

        is_structured = (
            heading_count > 0
            or table_line_count > 0
            or list_item_count > 0
        )

        return DocumentProfile(
            char_count=char_count,
            paragraph_count=paragraph_count,
            max_paragraph_chars=max_paragraph_chars,
            heading_count=heading_count,
            table_line_count=table_line_count,
            list_item_count=list_item_count,
            is_structured=is_structured,
        )

    @staticmethod
    def _paragraphs(text: str) -> list[str]:
        return [
            paragraph
            for paragraph in re.split(r"\n\s*\n", text)
            if paragraph.strip()
        ]

    @staticmethod
    def _count_headings(text: str) -> int:
        return sum(
            1
            for line in text.splitlines()
            if re.match(r"^\s{0,3}#{1,6}\s+\S+", line)
        )

    @staticmethod
    def _count_table_lines(text: str) -> int:
        return sum(
            1
            for line in text.splitlines()
            if "|" in line
        )

    @staticmethod
    def _count_list_items(text: str) -> int:
        return sum(
            1
            for line in text.splitlines()
            if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S+", line)
        )
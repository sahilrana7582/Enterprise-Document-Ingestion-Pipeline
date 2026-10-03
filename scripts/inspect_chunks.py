"""Inspect how a chunker splits documents, and check that the result can be trusted.

    python -m scripts.inspect_chunks samples/long_policy.txt --show 5
    python -m scripts.inspect_chunks data --size 1000 --overlap 200

PATH is a file or a directory. Documents are loaded by the real ingestion pipeline
(same discovery, loaders, skips and duplicate handling as scripts/ingest.py), then
every unique document is chunked.

Checks per document: offsets match the document, no chunk is too big, no text is
lost, ids and order are correct. Checks across documents: chunk ids are unique,
and every chunk belongs to a document in the result.

Exit status: 0 all checks passed, 1 a check failed, 2 the path could not be read.
"""

import argparse
import sys
from pathlib import Path

from src.ingestion.chunking.model import ChunkerConfig
from src.ingestion.chunking.recursive import RecursiveChunker
from src.ingestion.exceptions import DiscoveryError
from src.ingestion.pipeline import IngestionPipeline

# Add new chunkers here as you build them (structure-aware, adaptive, ...).
CHUNKERS = {
    "recursive": RecursiveChunker,
}


def check_document(document, chunks, chunk_size):
    """Return a list of problems found for one document. An empty list means all good."""
    problems = []
    text = document.content

    if not chunks:
        return ["no chunks were produced"]

    # 1. Offsets: every chunk is exactly the slice of the document it claims to be.
    bad_offsets = 0
    for chunk in chunks:
        if text[chunk.start : chunk.end] != chunk.content:
            bad_offsets += 1
    if bad_offsets:
        problems.append(f"offsets: {bad_offsets} chunk(s) do not match their slice of the document")

    # 2. Size: no chunk is longer than chunk_size.
    too_big = 0
    for chunk in chunks:
        if len(chunk.content) > chunk_size:
            too_big += 1
    if too_big:
        problems.append(f"size: {too_big} chunk(s) are longer than {chunk_size}")

    # 3. Coverage: every non-whitespace character of the document is inside some chunk.
    covered = [False] * len(text)
    for chunk in chunks:
        for position in range(chunk.start, min(chunk.end, len(text))):
            covered[position] = True
    lost = 0
    for position, character in enumerate(text):
        if not covered[position] and not character.isspace():
            lost += 1
    if lost:
        problems.append(f"coverage: {lost} non-whitespace character(s) are in no chunk")

    # 4. Ids and order: unique ids, indexes 0..n-1, starts strictly increasing.
    ids = [chunk.id for chunk in chunks]
    if len(set(ids)) != len(ids):
        problems.append("ids: duplicate chunk ids inside the document")
    indexes = [chunk.index for chunk in chunks]
    if indexes != list(range(len(chunks))):
        problems.append("order: chunk indexes are not 0..n-1")
    for i in range(len(chunks) - 1):
        if chunks[i].start >= chunks[i + 1].start:
            problems.append(f"order: chunk {i + 1} does not start after chunk {i}")
            break

    return problems


def count_boundaries_without_overlap(chunks):
    count = 0
    for i in range(len(chunks) - 1):
        if chunks[i + 1].start >= chunks[i].end:
            count += 1
    return count


def describe_chunk(chunk):
    content = chunk.content
    if len(content) > 80:
        content = content[:40] + " ... " + content[-40:]
    line = f"      [{chunk.index}] {chunk.start}:{chunk.end}  {content!r}"
    section = chunk.metadata.get("section")
    if section:
        line += f"  (section: {section})"
    return line


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="a file or a directory")
    parser.add_argument("--chunker", choices=sorted(CHUNKERS), default="recursive")
    parser.add_argument("--size", type=int, default=800, help="maximum chunk length in characters")
    parser.add_argument("--overlap", type=int, default=100, help="overlap in characters")
    parser.add_argument("--show", type=int, default=0, help="chunks to print per document")
    args = parser.parse_args()

    if args.size <= 0 or args.overlap < 0 or args.overlap >= args.size:
        parser.error("need --size > 0 and 0 <= --overlap < --size")

    config = ChunkerConfig(chunk_size=args.size, chunk_overlap=args.overlap)
    chunker = CHUNKERS[args.chunker](config)

    try:
        result = IngestionPipeline().run(args.path)
    except DiscoveryError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    print(
        f"Loaded: discovered {result.discovered}, skipped {len(result.skipped)}, "
        f"failed {len(result.failures)}, duplicates {len(result.duplicates)}, "
        f"unique documents {len(result.documents)}"
    )
    for skipped_path in result.skipped:
        print(f"  skipped: {Path(skipped_path).name}")
    for failure in result.failures:
        print(f"  failed:  {failure.error_type}  {Path(failure.source).name}")
    for duplicate in result.duplicates:
        print(f"  duplicate: {Path(duplicate.source).name} (same text as {Path(duplicate.duplicate_of).name})")

    print(f"\nChunker: {args.chunker}  size={args.size}  overlap={args.overlap}\n")

    all_chunks = []
    any_problem = False

    for document in result.documents:
        chunks = chunker.chunk(document)
        all_chunks.extend(chunks)
        problems = check_document(document, chunks, args.size)
        name = document.metadata["filename"]

        if chunks:
            lengths = [len(chunk.content) for chunk in chunks]
            stats = f"min/avg/max {min(lengths)}/{sum(lengths) // len(lengths)}/{max(lengths)}"
            boundaries = len(chunks) - 1
            overlap_info = f"no-overlap boundaries {count_boundaries_without_overlap(chunks)}/{boundaries}"
        else:
            stats = "no chunks"
            overlap_info = ""

        status = "FAIL" if problems else "OK  "
        print(f"{status}  {name:<22} {len(document.content):>6} chars  {len(chunks):>4} chunks  {stats}  {overlap_info}")
        for problem in problems:
            print(f"      PROBLEM {problem}")
            any_problem = True
        for chunk in chunks[: args.show]:
            print(describe_chunk(chunk))

    print(f"\nTotals: {len(result.documents)} document(s), {len(all_chunks)} chunk(s)")

    # Checks across all documents.
    ids = [chunk.id for chunk in all_chunks]
    ids_unique = len(set(ids)) == len(ids)
    document_ids = {document.id for document in result.documents}
    orphans = 0
    for chunk in all_chunks:
        if chunk.document_id not in document_ids:
            orphans += 1

    print(f"  {'PASS' if ids_unique else 'FAIL'}  chunk ids are unique across all documents")
    print(f"  {'PASS' if orphans == 0 else 'FAIL'}  every chunk belongs to a document in the result")
    if not ids_unique or orphans:
        any_problem = True

    print("\nResult: " + ("a check FAILED" if any_problem else "all checks passed"))
    return 1 if any_problem else 0


if __name__ == "__main__":
    sys.exit(main())

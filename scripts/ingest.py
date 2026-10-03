"""Smoke script: load one or more .txt files and show what the loader produced.

    python scripts/ingest.py data/txt/*.txt
    python scripts/ingest.py data/txt/handbook.txt --preview 400

Exits with status 1 if any file failed to load. A failure on one file never
stops the others; that is the per-file error isolation the pipeline will build on.
"""

import argparse
import sys

from ingestion.exceptions import DocumentLoadError
from ingestion.loaders import TextLoader


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="files to load")
    parser.add_argument("--preview", type=int, default=120, help="characters of content to show")
    args = parser.parse_args()

    loader = TextLoader()
    loaded = failed = 0
    for path in args.paths:
        try:
            doc = loader.load(path)
        except DocumentLoadError as err:
            failed += 1
            print(f"FAIL  {type(err).__name__}: {err}")
            continue
        loaded += 1
        print(f"OK    {doc.metadata['filename']}  id={doc.id}")
        print(f"      {doc.metadata}")
        print(f"      {doc.content[: args.preview]!r}")

    print(f"\n{loaded} loaded, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

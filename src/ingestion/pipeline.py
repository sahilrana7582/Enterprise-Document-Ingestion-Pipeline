from pathlib import Path

from src.ingestion.discovery import discover_files
from src.ingestion.exceptions import DocumentLoadError
from src.ingestion.loaders.registry import LoaderRegistry, default_registry
from src.ingestion.models import (
    Document, FailureRecord, IngestionResult, DuplicateRecord
)


class IngestionPipeline:

    def __init__(self, loader_registry: LoaderRegistry | None = None, skip_duplicates: bool = True) -> None:
        if loader_registry is None:
            loader_registry = default_registry()
        self.load_registry = loader_registry
        self.skip_duplicates = skip_duplicates

    def run(self, root: str | Path) -> IngestionResult:
        root_path = Path(root).expanduser()

        failure_records: list[FailureRecord] = []
        duplicate_records: list[DuplicateRecord] = []
        skip_records: list[str] = []
        document_result: list[Document] = []
        seen_checksums: dict[str, str] = {}  # checksum -> source of the first file with that text

        discovered = 0


        # CallBack to captures the failure records
        def handle_failure_record(error: OSError) -> None:
            failure_records.append(
                FailureRecord(
                    source=str(error.filename or "unknown"),
                    error_type=type(error).__name__,
                    message=error.strerror or str(error),
                )
            )

        files_path_iterable = discover_files(
            root=root_path,
            on_error=handle_failure_record,
            ignore_hidden=True,
        )

        for file_path in files_path_iterable:
            discovered += 1

            loader = self.load_registry.loader_for(file_path)

            if loader is None:
                skip_records.append(str(file_path))
                continue

            try:
                document = loader.load(file_path)

                if document.checksum in seen_checksums and self.skip_duplicates:
                    duplicate_records.append(
                        DuplicateRecord(
                            source=document.source,
                            duplicate_of=seen_checksums[document.checksum]
                        )
                    )
                    continue

                document_result.append(document)
                seen_checksums[document.checksum] = document.source


            except DocumentLoadError as err:
                failure_records.append(
                    FailureRecord(
                        source=err.source,
                        message=err.message,
                        error_type=type(err).__name__,
                    )
                )

        return IngestionResult(
            discovered=discovered,
            failures=failure_records,
            skipped=skip_records,
            documents=document_result,
            duplicates=duplicate_records
        )
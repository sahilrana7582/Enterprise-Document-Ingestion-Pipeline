from src.ingestion.loaders.registry import LoaderRegistry
from pathlib import Path
from src.ingestion.models import IngestionResult, FailureRecord, Document
from src.ingestion.discovery import discover_files
from src.ingestion.exceptions import (
    DocumentLoadError
)
from typing import List

class IngestionPipeline:

    def __init__(self, loader_registry: LoaderRegistry | None = None) -> None:
        self.load_registry = loader_registry

    def run(self, root: str | Path) -> IngestionResult:
        root_path = Path(root).expanduser()
        failure_records: List[FailureRecord] = []
        skip_records: List[str] = []
        total_documents = 0
        document_result: List[Document] = []

        def handle_failure_record(error: OSError):
            f_record = FailureRecord(
                source=str(error.filename or "unknown"),
                error_type=type(error).__name__,
                message=error.strerror or str(error),
            )
            failure_records.append(f_record)

        files_path_iterable = discover_files(root=root_path, on_error=handle_failure_record, ignore_hidden=True)
        for file_path in files_path_iterable:
            loader = self.load_registry.loader_for(files_path_iterable)

            if loader is None:
                skip_records.append(str(file_path))
                continue

            try:
                documents = loader.load(file_path)
                total_documents += 1
                document_result.append(d)
            except DocumentLoadError as err: 
                failure_records.append(
                    FailureRecord(source=err.source, message=err.message, error_type=type(err).__name__)
                )
                continue

        return IngestionResult(discovered=total_documents, failures=failure_records, skipped=skip_records, documents=document_result)





"""Exceptions raised by the ingestion pipeline.

The hierarchy is a contract between loaders and the pipeline: a loader that
fails because of *a file's* problem raises a ``DocumentLoadError`` (or a
subclass). The pipeline catches that family per file, records it and carries
on. Anything else (a ``TypeError``, a ``KeyError``) is a bug in our code and
is deliberately left to crash loudly.
"""


class IngestionError(Exception):
    """Root of every exception this project raises on purpose."""

class LoaderError(IngestionError):
    """A loader is set up or wired wrongly. A startup problem, not a bad file.

    Deliberately NOT a ``DocumentLoadError``: the pipeline swallows those per
    file, and a misconfigured loader must stop the run instead.
    """


class LoaderRegistryError(LoaderError):
    """The loader registry was used wrongly.

    Carries ``loader_name`` (which loader) and ``message`` (what went wrong).
    """

    def __init__(self, loader_name: str, message: str) -> None:
        self.loader_name = loader_name
        self.message = message
        super().__init__(loader_name, message)

    def __str__(self) -> str:
        return f"{self.loader_name}: {self.message}"


class DocumentLoadError(IngestionError):
    """A single document could not be loaded.

    Carries ``source`` (which file) and ``message`` (what went wrong) so that
    logs, reports and retry queues don't have to parse text to find the file.
    """

    def __init__(self, source: str, message: str) -> None:
        self.source = source
        self.message = message
        # Pass the raw values (not a formatted string) so ``args`` matches
        # ``__init__``'s signature. Exceptions are pickled as ``cls(*args)``
        # when they cross a process boundary, so this keeps them picklable.
        super().__init__(source, message)

    def __str__(self) -> str:
        return f"{self.source}: {self.message}"


class UnsupportedFileTypeError(DocumentLoadError):
    """Raised when a loader is given a file whose extension it does not handle."""


class DocumentReadError(DocumentLoadError):
    """Raised when the OS refuses the read: file missing, is a directory, or permission denied."""


class DocumentDecodeError(DocumentLoadError):
    """Raised when the bytes of a file cannot be turned into text (binary data, unknown encoding)."""


class FileTooLargeError(DocumentLoadError):
    """Raised, before reading, when a file exceeds the loader's size limit."""


class EmptyDocumentError(DocumentLoadError):
    """Raised when a file loads fine but contains no usable content."""


class DuplicateLoaderError(LoaderRegistryError):
    """Raised when registering a loader for an extension another loader already owns."""


class DiscoveryError(IngestionError):
    """The place to ingest from (the root path) is missing, unreadable, or not a file or directory.

    A run-level failure, deliberately NOT a ``DocumentLoadError``: there is no
    single bad file to skip, so the run cannot start.
    """
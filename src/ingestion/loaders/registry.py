"""Maps file extensions to the loader that handles them."""

from pathlib import Path

from src.ingestion.exceptions import DuplicateLoaderError, LoaderRegistryError
from src.ingestion.loaders.base import BaseLoader
from src.ingestion.loaders.text import TextLoader


class LoaderRegistry:
    """Looks up the loader for a file by its extension.

    Holds one loader *instance* per lowercase extension. Instances rather than
    classes, so each can carry its own configuration (e.g. ``max_bytes``).
    Two loaders can never own the same extension, so "which loader runs?"
    never depends on import order.
    """

    def __init__(self) -> None:
        self._by_extension: dict[str, BaseLoader] = {}

    def register(self, loader: BaseLoader) -> None:
        """Register ``loader`` for every extension it declares.

        Raises:
            TypeError: ``loader`` is not a ``BaseLoader`` instance (for example
                the class itself, passed without parentheses).
            LoaderRegistryError: the loader declares no extensions, or one that
                is not lowercase with a leading dot.
            DuplicateLoaderError: another loader already owns one of its extensions.
        """
        if not isinstance(loader, BaseLoader):
            raise TypeError(f"register() needs a BaseLoader instance, got {loader!r}")

        loader_name = type(loader).__name__
        extensions = loader.supported_extensions
        if not extensions:
            raise LoaderRegistryError(loader_name, "declares no supported_extensions")

        # Phase 1: validate every extension BEFORE storing any. A loader can own
        # several extensions; if the 2nd one conflicts, the 1st must not be left
        # behind, or a failed register() would leave the registry half-updated.
        for extension in extensions:
            # loader_for() lowercases the file's suffix, so anything not already
            # lowercase here could never match.
            if len(extension) < 2 or not extension.startswith(".") or extension != extension.lower():
                raise LoaderRegistryError(
                    loader_name,
                    f"extension {extension!r} must be lowercase and start with '.' (e.g. '.txt')",
                )
            existing = self._by_extension.get(extension)
            if existing is not None:
                raise DuplicateLoaderError(
                    loader_name,
                    f"extension {extension!r} is already registered by {type(existing).__name__}",
                )

        # Phase 2: commit.
        for extension in extensions:
            self._by_extension[extension] = loader

    def loader_for(self, path: str | Path) -> BaseLoader | None:
        """Return the loader for ``path``'s extension, or ``None`` if there isn't one.

        ``None`` is an answer, not an error: the caller decides what "no loader"
        means (the pipeline treats it as "skipped").
        """
        return self._by_extension.get(Path(path).suffix.lower())

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        """Every registered extension, sorted so messages and reports are stable."""
        return tuple(sorted(self._by_extension))


def default_registry() -> LoaderRegistry:
    """Build a fresh registry with every built-in loader registered.

    A factory rather than a module-level global, so each pipeline gets its own
    registry and can be configured or extended without affecting the others.
    """
    registry = LoaderRegistry()
    registry.register(TextLoader())
    return registry

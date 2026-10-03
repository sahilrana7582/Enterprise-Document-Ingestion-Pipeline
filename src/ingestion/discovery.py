"""Finds the files an ingestion run should consider."""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from src.ingestion.exceptions import DiscoveryError

# Operating-system, Office and version-control debris. Never a real document,
# so it is skipped even when ``ignore_hidden=False``.
_IGNORED_NAMES = frozenset({".DS_Store", ".git", ".hg", ".svn", "__MACOSX", "Thumbs.db"})
_IGNORED_PREFIXES = ("~$",)  # Office lock files such as "~$report.docx"


def discover_files(
    root: str | Path,
    *,
    ignore_hidden: bool = True,
    on_error: Callable[[OSError], None] | None = None,
) -> Iterator[Path]:
    """Yield every regular file under ``root``, in a stable order.

    ``root`` may be a directory (walked recursively) or a single file, which is
    yielded as is: a file you name explicitly is never filtered out.

    Inside a directory walk:
      * Order is deterministic (directories and files sorted by name, a
        directory's own files before its subdirectories), so reports and
        "first copy wins" dedup are reproducible across runs and machines.
      * Hidden names (leading ``.``) are skipped unless ``ignore_hidden=False``.
        OS/Office/VCS debris is always skipped.
      * Symlinked directories are not followed (no loops). Symlinked files are
        yielded; broken links and special files (sockets, fifos) are not.
      * A directory that cannot be read is reported to ``on_error`` (if given)
        and the walk carries on.

    The root is validated when this function is *called*, not when the result is
    first iterated, so a bad root fails where the mistake was made.

    Raises:
        DiscoveryError: ``root`` is missing, unreadable, or neither a file nor
            a directory.
    """
    try:
        root = Path(root).expanduser()  # RuntimeError for an unknown "~user"
        mode = root.stat().st_mode
    except FileNotFoundError as exc:
        raise DiscoveryError(f"Discovery root does not exist: {root}") from exc
    except (OSError, RuntimeError) as exc:
        raise DiscoveryError(f"Cannot access discovery root {root}: {exc}") from exc

    if S_ISREG(mode):
        return iter((root,))
    if S_ISDIR(mode):
        return _walk(root, ignore_hidden, on_error)
    raise DiscoveryError(f"Discovery root is neither a file nor a directory: {root}")


def _walk(
    root: Path,
    ignore_hidden: bool,
    on_error: Callable[[OSError], None] | None,
) -> Iterator[Path]:
    # topdown=True is what makes pruning `dirs` below effective; followlinks=False
    # is what prevents symlink loops. Both are load-bearing, so they are explicit.
    for dirpath, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=on_error):
        # Slice-assign: os.walk only honours changes made to the list in place.
        dirs[:] = sorted(d for d in dirs if not _is_ignored(d, ignore_hidden))

        for name in sorted(files):
            if _is_ignored(name, ignore_hidden):
                continue
            path = Path(dirpath) / name
            if path.is_file():  # False for broken symlinks and special files
                yield path


def _is_ignored(name: str, ignore_hidden: bool) -> bool:
    if name in _IGNORED_NAMES or name.startswith(_IGNORED_PREFIXES):
        return True
    return ignore_hidden and name.startswith(".")

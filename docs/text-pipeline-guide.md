# Text-only ingestion system: build guide (Steps 4-11)

You code, I guide. Every step has the same shape:
**Goal / Files → Concepts → Design decisions → Implement → Verify → Pitfalls → Check yourself.**

No tests, as agreed. Instead, each step has a **Verify** section (REPL snippets and commands with the expected result) and uses your `data/` folder as the fixture. If you get stuck, paste your code and the exact output. I'll hint first and only take over if you ask.

## Import convention (this project's rule)

The project imports itself as **`src.ingestion.…`**, everywhere, with no exceptions:

```python
from src.ingestion.exceptions import DiscoveryError
from src.ingestion.loaders.base import BaseLoader      # inside the loaders package: import the module, not the package
```

- **Never mix `ingestion.…` and `src.ingestion.…`.** They load as two separate copies of every module, so the same exception class exists twice and `except` stops catching what you raise.
- **Run everything from the project root.** `src` is only importable when the root is on `sys.path`. Scripts therefore run as modules: `python -m scripts.ingest …`. (`python scripts/ingest.py` puts `scripts/` on the path instead and fails with `No module named 'src'`.)
- The editable install from Step 1 still exposes a top-level `ingestion` package. Don't use that name anywhere. A quick audit: `grep -rnE "^\s*(from|import)\s+ingestion" src scripts` must print nothing.

---

## Step 0: Save what you have (2 min)

`base.py`, `text.py` and your new data files are still untracked.

```bash
git add -A && git commit -m "feat: BaseLoader, TextLoader, sample corpus"
```

Commit again at the end of every step below.

---

## The target

```
root dir ──► discover_files() ──► registry.loader_for(path) ──► loader.load(path) ──► Document
                │                        │ None                       │ DocumentLoadError
                │                        ▼                            ▼
                │                    skipped[]                    failures[]
                ▼
        IngestionResult(documents, failures, skipped, duplicates, metrics)  ──►  CLI summary + JSON report
```

Files at the end of this phase:

```
src/ingestion/
├── exceptions.py        + DiscoveryError
├── models.py            + checksum field, FailureRecord, DuplicateRecord, IngestionResult
├── discovery.py         NEW (Step 5)
├── pipeline.py          NEW (Step 6)
└── loaders/
    ├── base.py          refactored into a template method (Step 7)
    ├── text.py          slimmed down to the text-specific part
    └── registry.py      NEW (Step 4)
scripts/ingest.py        upgraded CLI (Step 10)
```

### Your fixture and what each file should become

| File | Expected fate | Why it's in the fixture |
|---|---|---|
| `markdown/team_notes.md` | **skipped** | no loader registered for `.md` yet |
| `txt/corrupt_binary.txt` | **failed**: `DocumentDecodeError` | NUL bytes |
| `txt/empty.txt` | **failed**: `EmptyDocumentError` | zero bytes |
| `txt/engineering/oncall/escalation.txt` | loaded | nested directory |
| `txt/engineering/runbook.txt` | loaded | nested directory |
| `txt/handbook.txt` | loaded | first of the duplicate group |
| `txt/handbook_copy.txt` | loaded, then **duplicate** | identical bytes |
| `txt/legacy_cp1252.txt` | loaded | encoding fallback |
| `txt/windows_crlf.txt` | loaded, then **duplicate** | different bytes, same text |
| `txt/with_bom.txt` | loaded | BOM |

**Final acceptance numbers** for `python -m scripts.ingest data/`:
discovered **10** · skipped **1** · failed **2** · loaded **7** · duplicates **2** · unique documents **5**.

You'll aim at these numbers from Step 6 onward. (I computed them by running your current loader on the fixture.)

---

## Step 4: Loader registry (about 30 min)

**Goal:** given a path, return the right loader without any `if ext == ".txt"` chains.
**File:** `src/ingestion/loaders/registry.py`

### Concepts
- **Registry pattern.** A lookup table (`extension → loader`) filled at startup. Adding PDF later becomes "register one more loader". No other file changes. That property is the entire point of this phase.
- **You register instances, not classes.** A `TextLoader(max_bytes=10_000_000)` carries configuration. A class can't.

### Design decisions
| Decision | Recommendation | Why |
|---|---|---|
| Two loaders claim `.txt` | `register()` raises `ValueError` | Silent overwrite means "which loader runs?" depends on import order. Fail at startup, not at 3am. |
| Unknown extension | `loader_for()` returns `None`, **doesn't raise** | The pipeline decides what "no loader" means: skipped, not failed. A registry just answers a question. |
| Global singleton registry | **No.** Provide a `default_registry()` *factory* | Globals make configuration and experiments painful. A fresh registry per pipeline is trivial. |
| Extension matching | lowercase the suffix | `REPORT.TXT` must work. Dotfiles like `.bashrc` have suffix `''`, so they match nothing. |

### Implement (skeleton, you fill the bodies)
```python
class LoaderRegistry:
    def __init__(self) -> None:
        self._by_extension: dict[str, BaseLoader] = {}

    def register(self, loader: BaseLoader) -> None: ...          # loop over loader.supported_extensions
    def loader_for(self, path: str | Path) -> BaseLoader | None: ...
    @property
    def supported_extensions(self) -> tuple[str, ...]: ...        # sorted, useful for CLI help/messages

def default_registry() -> LoaderRegistry: ...                     # registry + TextLoader()
```
Also export both names from `loaders/__init__.py`. In `register()`, make the duplicate error name both loaders (`'.txt' is already registered by TextLoader`), because an error naming only one side is half a clue.

### Verify (REPL)
```python
from src.ingestion.loaders import default_registry, TextLoader
r = default_registry()
r.supported_extensions            # ('.txt',)
r.loader_for("a/B.TXT")           # a TextLoader instance
r.loader_for("notes.md")          # None
r.loader_for("README")            # None
r.loader_for(".bashrc")           # None
r.register(TextLoader())          # ValueError naming '.txt'
```

### Check yourself
1. Why does `loader_for` return `None` instead of raising `UnsupportedFileTypeError`?
2. What breaks if two modules each build their own global registry?

**Commit:** `feat: loader registry`

---

## Step 5: Discovery (about 1 hr)

**Goal:** walk a directory and yield every candidate file, in a stable order, without ever crashing on a weird folder.
**Files:** `src/ingestion/discovery.py`, plus `DiscoveryError` in `exceptions.py`

### Concepts: the traps I tested on your machine
1. **`os.walk` silently yields nothing** for a nonexistent root *and* for a file passed as the root. A typo'd path would "succeed" with 0 files. **Validate the root yourself.**
2. **Listing order is arbitrary** (filesystem-dependent). Unsorted discovery means reports and "first copy wins" dedup differ between machines and runs. **Sort dirs and files.**
3. **`dirs[:] = ...` (in-place slice assignment)** is how you prune the walk. Rebinding `dirs = [...]` does nothing.
4. **Symlinked directories** show up in `dirs` but `os.walk` doesn't descend into them by default (`followlinks=False`). Keep that default; following links is how you get infinite loops. Symlinked *files* appear in `files`, and that's fine.
5. **Unreadable subdirectories** are silently skipped unless you pass `onerror=`.
6. **Use `os.walk`, not `Path.walk`.** `Path.walk` exists only on Python 3.12+, and your `pyproject.toml` promises 3.10+.
7. **Generators.** Yield paths lazily so a million-file tree doesn't build a million-element list up front.

### Design decisions
- Discovery is **generic**: it yields *all* regular files. It does **not** know about loaders. Filtering by extension would hide "skipped" files from the report, so that's the pipeline's job.
- Two kinds of ignored names, kept in **one** helper (`_is_ignored`) so the rules can't drift apart between files and directories:
  - *Hidden* (leading `.`), controlled by `ignore_hidden: bool = True`. Someone may genuinely want `.notes.txt`.
  - *Debris*, **always** ignored even with `ignore_hidden=False`: `.DS_Store`, `.git`/`.hg`/`.svn`, Office lock files starting with `~$` (they'll bite you with DOCX), `__MACOSX`, `Thumbs.db`. Without this, turning off `ignore_hidden` would start ingesting the contents of `.git/`.
- If `root` is a **file**, yield just that file, **even if its name is hidden**. You named it explicitly, and silently returning nothing would be baffling.
- Root missing, or neither file nor directory → raise **`DiscoveryError(IngestionError)`**. It's a run-level failure, not a per-file one, so it must **not** be a `DocumentLoadError`. This is the "room at the top of the hierarchy" I mentioned in Step 2.
- Subdirectory errors → call an optional `on_error(OSError)` callback and **keep going**. A callback keeps discovery decoupled from logging and results; the pipeline will pass one that records the problem.

### Implement
```python
def discover_files(
    root: str | Path,
    *,
    ignore_hidden: bool = True,
    on_error: Callable[[OSError], None] | None = None,
) -> Iterator[Path]: ...
```
Hints:
- Validate first, and **eagerly**. A function containing `yield` is a generator, and its body (including your `raise`) doesn't run until the first `next()`, so `files = discover_files("typo")` would "succeed" and the error would surface far from the mistake. Make `discover_files` a *normal* function that validates immediately and returns an iterator produced by a private generator (`_walk`).
- Do the validation with a single `stat()` (`S_ISREG` / `S_ISDIR` from the `stat` module) rather than `exists()` + `is_file()` + `is_dir()`, and catch `OSError` and `RuntimeError` as well as "not found". An unreadable parent directory raises `PermissionError`, and `~nosuchuser` raises `RuntimeError`. Both must come out as `DiscoveryError`.
- Inside the walk: prune `dirs`, sort `dirs`, loop over `sorted(files)`, skip ignored names, and only yield real files. A broken symlink named `x.txt` is in `files` but `is_file()` is `False`.
- Yield `Path` objects (join `dirpath` and name), not strings.

### Verify
Build a hostile demo tree:
```bash
D=$(mktemp -d)
mkdir -p "$D"/{a/deep,b,.git,locked}
touch "$D/top.txt" "$D/.DS_Store" "$D/.git/config" "$D/a/one.txt" "$D/a/deep/two.txt" "$D/b/Z.TXT" "$D/b/~\$lock.docx" "$D/locked/secret.txt"
ln -s "$D" "$D/a/loop"        # symlink back to an ancestor: an infinite loop if followed
chmod 000 "$D/locked"
echo $D
```
```python
from src.ingestion.discovery import discover_files
errs = []
[str(p) for p in discover_files("<paste $D>", on_error=errs.append)]
# expect, in this order: top.txt, a/one.txt, a/deep/two.txt, b/Z.TXT
# and NOT: .DS_Store, .git/config, ~$lock.docx, anything under loop/, anything under locked/
errs        # one PermissionError for 'locked'
list(discover_files("data"))      # your 10 fixture files, markdown/ first, then txt/ (files before subfolders)
list(discover_files("data/txt/handbook.txt"))   # just that file
list(discover_files("no/such/dir"))             # DiscoveryError (not an empty list!)
```
Clean up: `chmod 755 "$D/locked" && rm -rf "$D"`.

Run `list(discover_files("data"))` twice and confirm the same order each time.

### Check yourself
1. Why is a missing root a different exception *type* from a corrupt file?
2. Why sort, if the run "works" without sorting?
3. What would happen to this tree if you set `followlinks=True`?

**Commit:** `feat: directory discovery`

---

## Step 6: The pipeline and its result (about 1 hr)

**Goal:** `discover → pick loader → load → collect`, isolating every per-file failure.
**Files:** `src/ingestion/pipeline.py`; new data types in `models.py`

### Concepts
- **Error isolation.** In Step 2 you built the contract: loaders raise `DocumentLoadError` for file problems. The pipeline catches **exactly that** family. A `ValueError` from a bug must crash the run. You'll prove this below.
- **Skipped ≠ failed.** Skipped: "no loader for `.md`": expected, informational. Failed: "we tried and the file was bad": needs attention. Mixing them makes the failure count meaningless.
- **The result is data, not a printout.** The pipeline returns an object and prints nothing. The CLI, a JSON report, or a web endpoint can all consume it.

### Design decisions
```python
@dataclass(frozen=True, kw_only=True)
class FailureRecord:
    source: str
    error_type: str      # type(err).__name__
    message: str

@dataclass(kw_only=True)
class IngestionResult:
    discovered: int = 0
    documents: list[Document] = field(default_factory=list)
    failures: list[FailureRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
```
- **Why `error_type: str` and not the exception object?** The report must be JSON-serializable. Exception objects also hold tracebacks, which keeps frames alive in memory for the whole run. Convert the exception to plain strings at the boundary.
- **Why `IngestionResult` is mutable here:** the pipeline builds it up as it goes. That's fine, and `Document` itself stays frozen.
- **Memory.** `documents` holds everything in memory. Fine for thousands of files. A streaming/generator version is a Project-9 topic. Make a one-line note in the docstring so future-you remembers it's a known limit.

```python
class IngestionPipeline:
    def __init__(self, registry: LoaderRegistry | None = None) -> None: ...   # None → default_registry()
    def run(self, root: str | Path) -> IngestionResult: ...
```

### Implement `run()`
1. `discover_files(root, on_error=...)`. For now, collect the on_error problems as `FailureRecord`s too (`err.filename` holds the directory, and `error_type` is the exception class name).
2. For each path: `loader = registry.loader_for(path)`.
   - `None` → `skipped.append(str(path))`; `continue`.
3. `try: doc = loader.load(path)` / `except DocumentLoadError as err:` → append a `FailureRecord`; `continue`.
4. Append `doc` to `documents`. Count every discovered file in `discovered`.

### Verify
```python
from src.ingestion.pipeline import IngestionPipeline
r = IngestionPipeline().run("data")
(r.discovered, len(r.skipped), len(r.failures), len(r.documents))   # (10, 1, 2, 7)
[(f.error_type, f.source.split('/')[-1]) for f in r.failures]
# [('DocumentDecodeError', 'corrupt_binary.txt'), ('EmptyDocumentError', 'empty.txt')]
r.skipped      # ['.../data/markdown/team_notes.md']
```
**Prove the isolation boundary.** Temporarily add `raise ValueError("boom")` as the first line of `TextLoader.load`, run the pipeline, and confirm the *whole run crashes* with a `ValueError` traceback. Then remove the line. If it didn't crash, you're catching too much.

### Pitfalls
- `except Exception:` in the pipeline. It hides bugs. (There's one legitimate place for a broad catch, which I explain in Step 11.)
- Forgetting `continue` and appending a half-built document.
- Putting `print` in the pipeline. Output belongs to the CLI.

### Check yourself
1. Why does the pipeline catch `DocumentLoadError` and not `Exception`?
2. Why store `error_type` as a string?

**Commit:** `feat: ingestion pipeline with per-file error isolation`

---

## Step 7: Refactor `BaseLoader` into a template method (about 1-1.5 hrs)

**Goal:** make adding PDF/DOCX/HTML loaders cost ~30 lines each, instead of copy-pasting 90 lines of file handling.

### Concepts: why now
Look at `TextLoader.load()`: only **steps 7-8** (NUL check, decoding, newline normalization) are about *text*. Everything else is about *any file*:

```
shared (every format)                      format-specific
─────────────────────                      ───────────────
1 extension check                          _extract(raw: bytes, path) → content + extra metadata
2 resolve path
3 stat, regular file, size limit
4 read bytes
        ──► self._extract(raw, path) ◄──
5 empty check
6 build id + metadata + Document
```
This is the **Template Method** pattern: the base class owns the algorithm skeleton, and subclasses fill in one hook. PDF will implement `_extract` with a PDF library, DOCX with python-docx, and so on. The file-handling, errors and metadata come free.

### Safety net first (we have no tests, so use a golden-output diff)
`scripts/ingest.py` prints metadata as a dict, so key order could cause false diffs. First change that one line to print `dict(sorted(doc.metadata.items()))`. Then capture the baseline **before touching anything**:
```bash
python -m scripts.ingest data/txt/*.txt > /tmp/before.txt
```
After the refactor:
```bash
python -m scripts.ingest data/txt/*.txt > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt && echo IDENTICAL
```
A pure refactor must produce **IDENTICAL**. Any diff is a behavior change that you introduced by accident.

### Design
```python
class Extracted(NamedTuple):
    content: str
    metadata: dict[str, Any]            # format-specific: {"encoding": "utf-8"} for text, {"page_count": 12} for PDF

class BaseLoader(ABC):
    source_type: ClassVar[str]
    supported_extensions: ClassVar[tuple[str, ...]]

    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES) -> None: ...      # moves up from TextLoader
    def load(self, path: str | Path) -> Document: ...                        # CONCRETE: the shared algorithm
    @abstractmethod
    def _extract(self, raw: bytes, path: Path) -> Extracted: ...             # the one hook
    def supports(self, path: str | Path) -> bool: ...                        # unchanged
```
- **What moves to `base.py`:** `DEFAULT_MAX_BYTES`, `_resolve`, `_describe_os_error`, `_stat_regular_file`, `_read_bytes`, `_document_id`, the constructor, and the metadata building. Shared metadata keys: `filename`, `extension`, `size_bytes`, `modified_at`, `char_count`. Merge the format-specific `Extracted.metadata` on top.
- **What stays in `text.py`:** the encodings list, `_decode`, `_normalize_newlines`, and a `_extract` that does: NUL check → decode → normalize → `Extracted(content, {"encoding": label})`.
- **Convention:** subclasses implement `_extract` and **don't override `load`**. Python won't enforce that, so write it in the class docstring.
- **Where does the empty check live?** In the shared part, *after* `_extract`. This will quietly give you the right behavior for scanned PDFs later: no extractable text → `EmptyDocumentError`.
- **The source string for errors inside `_extract`:** the resolved path is passed in, so use `str(path)`.

### Verify
- The golden diff prints **IDENTICAL**.
- Re-run the Step 6 check: still `(10, 1, 2, 7)`.
- `BaseLoader()` still can't be instantiated, and a subclass that forgets `_extract` fails with `TypeError` at instantiation (try it in the REPL with a 3-line throwaway class).

### Pitfalls
- Leaving a `load` in `TextLoader` that shadows the base one. Delete it, don't keep both.
- Computing `char_count` on the text *before* newline normalization. `windows_crlf.txt` would report 373 instead of 363, and the golden diff will catch it. Order is behavior: the NUL check runs on raw bytes, then decode, then normalize, and only then the empty check and `char_count`.
- Keeping `max_bytes` validation only in `TextLoader.__init__`. Call `super().__init__(max_bytes)`.

### Check yourself
1. Which lines of the old `TextLoader.load` were *really* about text?
2. If PDF's `_extract` raises a random library exception on a corrupt file, what happens in the pipeline? (Hold the thought for Step 11.)

**Commit:** `refactor: template-method BaseLoader`

---

## Step 8: Checksums and duplicate detection (about 45 min)

**Goal:** separate **identity** from **version**, and detect duplicate content within a run.
**Files:** `models.py`, `loaders/base.py`, `pipeline.py`

### Concepts: two different questions
| | Question | Source | Changes when… |
|---|---|---|---|
| `id` | *Where does this document live?* | hash of the path | the file is moved or renamed |
| `checksum` | *What does this document say?* | hash of the content | the text changes |

Incremental ingestion (Project 6) is built on exactly this pair:
- same `id`, same `checksum` → unchanged, skip
- same `id`, new `checksum` → edited, re-embed
- new `id` → added
- `id` gone from the source → deleted

You're laying the foundation now. You don't build incremental ingestion yet.

### Design decisions
- **Hash the normalized *content*, not the raw bytes.** Your fixture proves why: `handbook.txt` and `windows_crlf.txt` are different files (different bytes) with the same text. A byte hash calls them different, and you'd embed the same text twice. A content hash correctly calls them duplicates. Use `sha256(content.encode("utf-8")).hexdigest()` on the final, normalized text. (A raw-bytes `file_sha256` is useful for integrity checks, but it isn't what dedup needs. Skip it for now.)
- **`checksum` becomes a first-class `Document` field**, not a metadata entry. It's part of the document's identity story. Add it to `__post_init__`'s non-empty string checks. Compute it **once, in `BaseLoader.load`**, so no format can forget it. That's the payoff of Step 7.
- **Duplicate policy:**
  - The **first** file seen wins. Sorted discovery (Step 5) makes "first" deterministic.
  - Later copies are recorded in `result.duplicates` and, by default, **left out of `documents`**.
  - Never drop silently: the report must list what was dropped and why.
  - `IngestionPipeline(skip_duplicates=True)`. Setting it to `False` keeps them in `documents` but still reports them.
- **Exact duplicates only.** Near-duplicates (a doc with one paragraph changed) need MinHash or embeddings. That's later.

```python
@dataclass(frozen=True, kw_only=True)
class DuplicateRecord:
    source: str
    duplicate_of: str
```
Add `duplicates: list[DuplicateRecord]` to `IngestionResult`. In `run()`, keep a `seen: dict[str, str]` (checksum → first source).

### Verify
```python
loader = TextLoader()
a = loader.load("data/txt/handbook.txt")
b = loader.load("data/txt/windows_crlf.txt")
c = loader.load("data/txt/handbook_copy.txt")
a.checksum == b.checksum == c.checksum      # True
len({a.id, b.id, c.id})                     # 3  (three different locations)

r = IngestionPipeline().run("data")
(len(r.documents), len(r.duplicates))       # (5, 2)
[(d.source.split('/')[-1], d.duplicate_of.split('/')[-1]) for d in r.duplicates]
# [('handbook_copy.txt', 'handbook.txt'), ('windows_crlf.txt', 'handbook.txt')]
```
Then run with `skip_duplicates=False` → `len(documents) == 7`, and duplicates are still listed.

### Pitfalls
- Hashing the *pre-normalization* text, which would make the CRLF twin non-duplicate.
- Computing the checksum in `TextLoader`. It would have to be re-done in every loader.
- Treating an *empty* result as a duplicate group. Empty docs fail earlier, so they never reach this step. Be sure you can say why.

### Check yourself
1. Why can two documents have different `id`s and the same `checksum`?
2. If someone edits one typo in `handbook.txt`, which of `id` / `checksum` changes?

**Commit:** `feat: content checksum and duplicate detection`

---

## Step 9: Logging and metrics (about 45 min)

**Goal:** make runs observable without polluting the library with `print`.
**Files:** `pipeline.py` (+ small additions to `models.py`)

### Concepts: logging
- A library never configures logging and never prints. Each module does `logger = logging.getLogger(__name__)`. **The application (the CLI) decides** the level, format and destination. That's what lets the same pipeline later run in a worker, a cron job, or a web service with different logging.
- Levels: `DEBUG` per-file detail · `INFO` run start/end · `WARNING` a file failed, skipped or duplicated · `ERROR` something unexpected.
- Use lazy formatting: `logger.warning("failed %s: %s", source, err)`, not f-strings, so the string is only built if the message is emitted.
- Include the **source path** in every message about a file. Log **metadata, never content**: documents can contain personal or confidential data, and logs are widely readable and long-lived.

### Concepts: metrics
Derive them from the result at the end of the run instead of maintaining counters in the loop. There's less mutable state to get wrong. Add:
```python
@dataclass(frozen=True, kw_only=True)
class IngestionMetrics:
    discovered: int
    loaded: int            # all successfully loaded, including duplicates
    unique: int            # len(documents)
    skipped: int
    failed: int
    duplicates: int
    total_chars: int
    total_bytes: int
    duration_seconds: float
    failures_by_type: dict[str, int]      # {"DocumentDecodeError": 1, "EmptyDocumentError": 1}
    documents_by_type: dict[str, int]     # {"txt": 5}
```
- Time with `time.perf_counter()`, which is monotonic. Don't use `time.time()` for durations because it can jump when the clock is adjusted.
- `collections.Counter` gives you the two `*_by_type` dicts almost for free.
- Why this matters beyond demos: "failure rate went from 0.5% to 8%" is how you notice that a new document template broke your parser.
- To compute `total_chars` / `total_bytes` for *loaded-including-duplicates* you need the sizes of duplicates too. Decide whether the metric is over unique docs or all loaded, and **document your choice** in the dataclass. (Simplest honest choice: unique documents only, and say so.)

### Implement
- Module logger in `pipeline.py` (and in `discovery.py` if you want to log pruned/ignored dirs at DEBUG).
- Log: INFO at start (`root`, registered extensions) and at end (the summary line); DEBUG per loaded file; WARNING per failure/skip/duplicate.
- A `result.metrics` property (or a `build_metrics(result, duration)` function) and a `duration_seconds` stored on the result.

### Verify
```python
import logging
logging.basicConfig(level=logging.DEBUG, format="%(levelname)-7s %(name)s: %(message)s")
r = IngestionPipeline().run("data")
r.metrics
# discovered=10 loaded=7 unique=5 skipped=1 failed=2 duplicates=2,
# failures_by_type={'DocumentDecodeError': 1, 'EmptyDocumentError': 1}, documents_by_type={'txt': 5}
```
- With level `WARNING` you see only the problem files; with `DEBUG` you see everything.
- Without any `basicConfig`, importing and running the library prints nothing. That's the correct behavior.
- `grep` your output: no document *text* appears in any log line.

### Check yourself
1. Why does a library not call `logging.basicConfig`?
2. Why derive metrics from the result instead of counting in the loop?

**Commit:** `feat: logging and run metrics`

---

## Step 10: The CLI and JSON report (about 45 min)

**Goal:** a real command-line tool that a cron job or CI can run and *act on*.
**File:** `scripts/ingest.py` (rewrite it around the pipeline)

### Design
```
python -m scripts.ingest data/                          # summary only
python -m scripts.ingest data/ -v                       # per-file DEBUG logging
python -m scripts.ingest data/ --report report.json     # machine-readable output
python -m scripts.ingest data/ --keep-duplicates
```
- `argparse`: positional `root` (file or directory), `-v/--verbose`, `--report PATH`, `--keep-duplicates`.
- This is the **only** place that calls `logging.basicConfig` (INFO by default, DEBUG with `-v`) and the only place that prints.
- **Exit codes** are a contract with whatever runs your script:
  | Code | Meaning |
  |---|---|
  | `0` | everything loaded (skips and duplicates are fine) |
  | `1` | the run completed but some files **failed** |
  | `2` | the run could not start (`DiscoveryError`: bad root). `argparse` usage errors also exit 2 |

  A scheduler or CI job can't read your printout, but it can read the exit code.
- Catch `DiscoveryError` in the CLI → print one clean line to **stderr**, exit 2. No traceback for expected errors.

### The report
Build it with a `result.to_report() -> dict`:
- `metrics` (all fields)
- `failures` (list of `{source, error_type, message}`)
- `skipped`, `duplicates`
- `documents`: for each, `id`, `source`, `source_type`, `checksum`, `metadata` — **not `content`**. A report is for auditing; megabytes of text don't belong in it.

`dataclasses.asdict` converts nested dataclasses for you. Write with `json.dump(..., indent=2, sort_keys=True)`, and add a `generated_at` UTC ISO timestamp (this is the one field expected to differ between runs).

### Verify
```bash
python -m scripts.ingest data/ ; echo "exit=$?"                       # summary, exit=1 (two failures)
python -m scripts.ingest data/txt/engineering ; echo "exit=$?"        # exit=0
python -m scripts.ingest no/such/dir ; echo "exit=$?"                 # one clean error line, exit=2, no traceback
python -m scripts.ingest data/ --report /tmp/report.json
python -m json.tool /tmp/report.json | head -40                       # valid JSON, no "content" key anywhere
python -m scripts.ingest data/ -v 2>&1 | grep -c DEBUG                # > 0
```

### Pitfalls
- Printing the summary with `print` *and* logging it. Pick one channel per message.
- Dumping `Document` objects into JSON, which includes `content` and fails on non-serializable values.
- Returning exit `0` when files failed, which makes the failure invisible to automation.

**Commit:** `feat: CLI with report and exit codes`

---

## Step 11: Acceptance run, and your recipe for the next formats (about 30 min)

### Acceptance checklist
- [ ] `python -m scripts.ingest data/` → **discovered 10 · skipped 1 · failed 2 · loaded 7 · duplicates 2 · unique 5**, exit code 1
- [ ] Running it twice gives identical reports (apart from `generated_at` and `duration_seconds`)
- [ ] An absolute path and a relative path give the **same ids**: `python -m scripts.ingest "$PWD/data"` vs `python -m scripts.ingest data`. (Why? The loader resolves to an absolute path.)
- [ ] The injected-`ValueError` check from Step 6 still crashes the run
- [ ] `git status` is clean and every step has its own commit

When this is green, **the text system is done.** Merge the branch into `main` when you're happy.

### Recipe: adding a new format (PDF, DOCX, HTML…)
This is the payoff of Steps 4 and 7. For each new format you should touch **exactly three places**, and neither `pipeline.py` nor `discovery.py` is one of them:

1. **New file** `loaders/<format>.py`: subclass `BaseLoader`, set `source_type` and `supported_extensions`, implement `_extract(raw, path) -> Extracted`.
2. **Register it** in `default_registry()`.
3. **Add fixtures** to `data/`: one good file, one corrupt, one empty, and a format-specific nasty one.

If you ever need to edit the pipeline to support a format, the abstraction leaked. Come and tell me.

**The legitimate broad `except`.** Third-party parsers raise arbitrary things on malformed files (`KeyError`, `struct.error`, library-specific errors). The pipeline must keep catching only `DocumentLoadError`, so the **loader is the boundary**. Inside `_extract`, wrap the parser call:
```python
try:
    ...parse with the library...
except Exception as exc:                      # third-party code: we can't enumerate what it raises
    raise DocumentDecodeError(str(path), f"cannot parse PDF: {exc}") from exc
```
Catch broadly *around the library call only*, convert to our type, and keep `from exc` so the real cause isn't lost.

**Format-specific heads-up** (research each when you get there):
| Format | Watch out for |
|---|---|
| PDF | scanned PDFs have no text layer → empty text → `EmptyDocumentError` (OCR is a later project); encrypted PDFs; page-level metadata (`page_count`); `pypdf` vs `PyMuPDF` quality and speed |
| DOCX | Office lock files `~$*.docx` (Step 5 already ignores them); tables and headers aren't in the paragraph text by default; `.doc` ≠ `.docx` |
| HTML | strip `<script>`/`<style>` and navigation boilerplate; the charset is declared in the page, not guaranteed UTF-8; keep `<title>` as metadata |
| Markdown | can reuse most of the text loading with `source_type = "md"`; decide whether to keep or strip markup (this is where chunking needs headings) |
| CSV/JSON | structured data; you must *decide* how to turn rows or objects into text, and that decision shapes retrieval quality |

### Deliberately **not** in this phase
Chunking (next project) · parallel/async loading · persisting state between runs (incremental ingestion) · config files · near-duplicate detection · OCR · validators beyond "not empty".

---

## Time budget and how to ask me for help

Roughly **6-8 hours** total across Steps 4-11. Don't try to do it in one sitting.

Stuck? Send: the step number, your code, and the **exact** error or unexpected output. I'll go **hint → pointer to the line → explanation → fix**. Say "take over" for a step and I'll write it.

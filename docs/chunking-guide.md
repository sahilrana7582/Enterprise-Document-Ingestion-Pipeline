# Chunking engine: build guide (Step 9)

You write the code, I guide. Same rules as before: `src.ingestion.…` imports, every model extends `FrozenModel`, plain Python, no tests (the inspection script in S4 is your safety net).

## The design

```
Document ─► DocumentAnalyzer ─► DocumentProfile ─┐
                                                 ▼
                                       AdaptiveChunker (router)
                                   ┌─────────────┴───────────────┐
                              structured?                    plain text?
                                   ▼                             ▼
                        StructureAwareChunker            RecursiveChunker
                         (sections at headings;        (separator hierarchy,
                          big sections → recursive)      merge, overlap)
                                   └─────────────┬───────────────┘
                                                 ▼
                                             list[Chunk]
```

Every chunker is a `Chunker`: it takes a `Document` and returns `list[Chunk]`. Semantic, parent-child and table chunkers plug into the same slot later.

```
src/ingestion/chunking/
├── __init__.py
├── models.py       Chunk, ChunkerConfig, Piece, DocumentProfile          (S1)
├── base.py         Chunker: shared template                              (S2)
├── recursive.py    RecursiveChunker                                      (S3)
├── analyzer.py     DocumentAnalyzer                                      (S5)
├── structure.py    StructureAwareChunker                                 (S6)
└── adaptive.py     AdaptiveChunker, the router                           (S7)
scripts/inspect_chunks.py                                                 (S4)
```

### Decisions (and why)

| Decision | Why |
|---|---|
| `Chunker` is a template method, like `BaseLoader`. Subclasses implement only `_split(document) -> list[Piece]`. | The shared code (strip, drop blanks, find offsets, ids, metadata) is written once, so no chunker can forget it. |
| `Piece(text, metadata)` is a plain `NamedTuple`, like `Extracted`. | A chunker can attach extra metadata (`section`) without the base class knowing about it. |
| Offsets come from `document.content.find(text, cursor)`, with `cursor = previous_start + 1`. | `_split` returns plain strings, which is simple to write. `+ 1`, not `+ len(text)`, because overlapping chunks start *before* the previous one ends. |
| **Invariant:** `document.content[chunk.start:chunk.end] == chunk.content` | It makes lineage, highlighting and citations trustworthy. The script in S4 checks it on every chunk. |
| Chunk id is `f"{document.id}-{index}"`. | Deterministic. When a document changes you replace *all* of its chunks (delete by `document_id`, insert again). That is the standard operational rule. |
| The router is itself a `Chunker`. Its `_split` picks a chunker and returns that chunker's pieces. | Nothing downstream knows a router exists. |
| The section heading goes in **metadata**, not into `content`. | `content` stays an exact slice of the document, so the invariant holds. At embedding time you can build `section + "\n" + content`. |
| Size is measured in characters now, through **one** method, `self._length(text)`. | Tokens come later by overriding that single method (see "Later"). |

### What I measured on your machine (so you can check your own numbers)

- Fixture: `samples/long_policy.txt` (byte-identical to your current `data/txt/handbook.txt`): **8,714 chars, 25 paragraphs, one 3,024-char paragraph with no line breaks, 8 headings, a 6-line table.**
- Token ratio with your embedding model's tokenizer (`text-embedding-3-small`, `cl100k_base`): **5.17 chars per token** on this document. An 800-char chunk is about 160 tokens, and the model accepts 8,191, so chunk size here is about retrieval quality, not the model's limit.
- Plain recursive, separators `["\n\n", "\n", ". ", " "]`, then hard cut: **800/100 → 15 chunks · 1000/200 → 11 · 300/50 → 44 · 100/20 → 127**. Zero chunks over size, zero lost non-whitespace characters.
- Structure-aware at 800/100: 7 sections (the 120-char title block is joined to section 1), giving **[1, 2, 2, 7, 2, 2, 1] chunks = 17 total**, and no chunk crosses a section boundary.
- LangChain's `RecursiveCharacterTextSplitter` with `separators=["\n\n","\n",". "," ",""]` gives the **same chunk counts** and the same average sizes (min size can differ by a character or two, because it keeps the separator at the *start* of the next piece and we keep it at the *end* of the previous one).

---

## S1: `chunking/models.py`

```python
class Chunk(FrozenModel):
    id: NonBlankStr
    document_id: NonBlankStr
    index: int = Field(ge=0)
    content: NonBlankStr = Field(repr=False)     # never log text
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

class ChunkerConfig(FrozenModel):
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)

class Piece(NamedTuple):
    text: str
    metadata: dict[str, Any]

class DocumentProfile(FrozenModel):
    char_count: int
    paragraph_count: int
    max_paragraph_chars: int
    heading_count: int
    table_line_count: int
    list_item_count: int
    is_structured: bool
```
Import `FrozenModel` and `NonBlankStr` from `src.ingestion.models`. `Chunk` is the first model outside `models.py`, so the dependency points one way only: chunking imports from ingestion, never the reverse.

**Why 800/100 as the default?** It's a *learning* default: chunks short enough that you can read every one. The production default becomes about 450 tokens once sizing is in tokens (see "Later").

**Verify:** `Chunk(id="d-0", document_id="d", index=0, content="  ", start=0, end=2)` must fail (blank content), and so must `Chunk(..., end=0)`.

## S2: `chunking/base.py`: the shared template

```python
class Chunker(ABC):
    def __init__(self, config: ChunkerConfig | None = None) -> None: ...
    def chunk(self, document: Document) -> list[Chunk]: ...         # CONCRETE
    @abstractmethod
    def _split(self, document: Document) -> list[Piece]: ...
    def _length(self, text: str) -> int: ...                        # returns len(text)
```

`__init__`:
1. `self.config = ChunkerConfig() if config is None else config`.
2. A plain `if self.config.chunk_overlap >= self.config.chunk_size: raise ValueError(...)`. This check is not optional: an overlap of at least the size would make the window never advance, so a fixed-size loop would run forever.

`chunk(document)`, in order:
1. `pieces = self._split(document)`
2. Set `cursor = 0` and `index = 0`.
3. For each piece: `text = piece.text.strip()`. If it is empty, skip it. **A whitespace-only piece really appears: 1 at 800/100 and 5 at 300/50.**
4. `start = document.content.find(text, cursor)`. If it returns `-1`, raise `ValueError`. A chunker returned text that isn't in the document, which is a bug in that chunker, so let it crash loudly.
5. `cursor = start + 1`.
6. Build the `Chunk`: `id=f"{document.id}-{index}"`, `index`, `end=start + len(text)`, and
   `metadata = {**document.metadata, "source": document.source, "source_type": document.source_type, **piece.metadata}`.
7. `index += 1`. Return the list.

Index counts only kept chunks, so indexes are `0..n-1` with no gaps.

## S3: `chunking/recursive.py`: build this first, and do it yourself

```python
class RecursiveChunker(Chunker):
    def __init__(self, config=None, separators=("\n\n", "\n", ". ", " ")) -> None: ...
    def split_text(self, text: str) -> list[str]: ...    # public: the structure-aware chunker reuses it
    def _split(self, document) -> list[Piece]: ...       # wraps split_text(document.content)
```

### The algorithm in plain words

`split_text(text, separators)`:

1. If `_length(text) <= chunk_size`: return `[text]`.
2. If there are no separators left: **hard cut**. Slice every `chunk_size` characters and return the slices.
3. Take `sep = separators[0]`. If `sep` is not in `text`, recurse with `separators[1:]` and return the result.
4. Split into pieces **keeping the separator attached to the end of each piece** (below).
5. Walk the pieces with a `small` list:
   - A piece **longer than `chunk_size`**: first flush `small` through `merge`, then recurse on that piece with `separators[1:]` and add its results.
   - Any other piece: append it to `small`.
6. At the end, flush `small` through `merge`.

`split_keep_separator(text, sep)`: `parts = text.split(sep)`. Every part except the last becomes `part + sep`. Keep the last part only if it isn't empty.

`merge(pieces)`: greedily grow chunks.
```
current = []
for piece in pieces:
    if current and length("".join(current + [piece])) > chunk_size:
        emit "".join(current)
        # carry the overlap: drop pieces from the FRONT until what's left is
        # no longer than chunk_overlap AND the new piece fits
        while current and (length("".join(current)) > chunk_overlap
                           or length("".join(current + [piece])) > chunk_size):
            current.pop(0)
    current.append(piece)
emit "".join(current) at the end
```
Because separators stay attached and pieces are joined with `""`, every chunk is a **contiguous slice** of the document. That is what makes the S2 offset search always succeed.

### Two bugs I hit in my own prototype (so you can avoid them)

1. **Dropping the separator loses characters.** My first version used `text.split(". ")` and joined with `". "`. It silently deleted the full stop at every chunk boundary: 4 characters lost at 800/100, 34 at 100/20. Keeping the separator on the end of the previous piece fixes it.
2. **A whitespace-only chunk appears** (a piece that was just `"\n\n"`). The shared `chunk()` drops it, so you don't handle it here.

### Facts about overlap you should know before you trust it

At 800/100 on your fixture, **12 of 14 chunk boundaries have no overlap at all**. Overlap here is built from *whole pieces* (paragraphs, sentences) that fit in `chunk_overlap` characters, and your paragraphs are bigger than 100 characters. At 300/50, it's 41 of 43. So `chunk_overlap` is a ceiling, not a promise. Your inspection script will report this number, and later your evaluation set will tell you whether it matters.

### Verify (after S4's script exists)

| `size/overlap` | chunks | | `size/overlap` | chunks |
|---|---|---|---|---|
| 800/100 | **15** | | 300/50 | **44** |
| 1000/200 | **11** | | 100/20 | **127** |

(With separators `("\n\n", "\n", ". ", " ")` and the hard-cut fallback.) The three invariants in S4 must pass at all four settings. Your recursion should reproduce my counts. If you're off by one or two, look for the whitespace-only chunk first.

**Then compare with LangChain.** Add `langchain-text-splitters>=1.1,<2` to the **dev** extra in `pyproject.toml` (it's a reference tool for now, not a runtime dependency) and run `pip install -e ".[dev]"`. In a REPL:
```python
from langchain_text_splitters import RecursiveCharacterTextSplitter
splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100,
                                          separators=["\n\n", "\n", ". ", " ", ""])
len(splitter.split_text(document.content))      # 15
```
Same counts means your implementation understands the algorithm. Then open one chunk from each side by side and find where they differ and why (the separator placement above).

### Check yourself
1. Why does `merge` measure `"".join(current + [piece])` instead of adding up lengths?
2. Why is the cursor `start + 1` and not `start + len(text)`?
3. What goes wrong if `chunk_overlap >= chunk_size`?

## S4: `scripts/inspect_chunks.py`: your safety net

```
python -m scripts.inspect_chunks samples/long_policy.txt --chunker recursive --size 800 --overlap 100 --show 5
```
Arguments: `path`, `--chunker recursive|structure|adaptive` (default `adaptive`; add the others as you build them), `--size`, `--overlap`, `--show N`. Load the file with `TextLoader`, chunk it, and print:

- the document length, the number of chunks, and min/avg/max chunk length,
- the first `N` chunks as `[index] start:end  "first 40 chars…last 40 chars"` plus their `section` if present,
- **four checks, each printing PASS or FAIL with the failing count:**
  1. **offset invariant:** `document.content[c.start:c.end] == c.content` for every chunk,
  2. **size:** no chunk longer than `chunk_size`,
  3. **coverage:** every non-whitespace character of the document lies inside at least one chunk (keep a list of `False` flags, one per character, and mark each chunk's range),
  4. **ids and order:** ids are unique, `index` runs `0..n-1`, and `start` strictly increases,
- and one informational line: how many boundaries have **no overlap**.

This script is how you'll know a change to any chunker is safe. Run it after every edit.

## S5: `chunking/analyzer.py`

```python
class DocumentAnalyzer:
    def analyze(self, document: Document) -> DocumentProfile: ...
```
Plain string rules, no regular expressions:
- **paragraphs:** `content.split("\n\n")`, keeping the non-blank ones.
- **heading line:** stripped, non-empty, at most 80 characters, and `.isupper()`. Python's `isupper()` ignores digits and punctuation, so `"1. PURPOSE AND SCOPE"` counts.
- **table line:** contains at least two `|` characters.
- **list item:** a line that starts with `"- "`, or with digits followed by `". "`.
- **`is_structured`:** `heading_count >= 2`.

**Verify:** the fixture should give `char_count 8714, paragraph_count 25, max_paragraph_chars 3024, heading_count 8, table_line_count 6, is_structured True`. The list-item count will be around 16, depending on exactly how you write the rule. `data/txt/engineering/runbook.txt`, `data/txt/engineering/oncall/escalation.txt` and `data/txt/windows_crlf.txt` all give `heading_count 0`, so `is_structured False`.

**Known limit:** your small files use mixed-case numbered headings like `1. Eligibility`, which this rule misses. That's fine for 300-character files. Recognizing them safely is a good exercise later: a short numbered line is *not* a heading when it's a list item, and both look alike (`4. Do not try to investigate…` is a list item).

## S6: `chunking/structure.py`

```python
class StructureAwareChunker(Chunker):
    def __init__(self, config=None, min_section_chars: int | None = None) -> None: ...   # holds a RecursiveChunker
    def _split(self, document) -> list[Piece]: ...
```
1. Find the heading lines **with their offsets**: loop over `content.split("\n")` and keep a running `position += len(line) + 1`. (The loader already normalized every newline to `"\n"`, which is why this arithmetic is safe.)
2. Sections run from one heading's offset to the next. If the text before the first heading isn't empty, it's a section of its own.
3. **Merge tiny sections.** A section shorter than `min_section_chars` (default `chunk_size // 4`, which is 200) is joined to the **next** section and takes that section's heading. Without this, the 120-character title block becomes a useless chunk.
4. For each section: if its length is at most `chunk_size`, it becomes one `Piece`. Otherwise run it through `self.recursive.split_text(section_text)` and make one `Piece` per result. Every piece carries `{"section": heading_text, "section_index": n}`.
5. A document with no headings: return what the recursive chunker would, so this chunker never fails on plain text.

**Verify** (800/100 on the fixture): **7 sections → chunks per section `[1, 2, 2, 7, 2, 2, 1]`, 17 total.** Each chunk has a `section`, and **no chunk contains text from two sections**. Add that as check 5 in the script: for every chunk, all its characters lie inside one section's range.

Then open section 3's chunks. The table is one block with no blank lines, so it should stay whole inside one chunk. Now try `--size 300` and watch what happens to it (I measured the plain recursive splitter at 300/50 putting the header row in one chunk and the last data rows in the next, so those rows lose their column names). This is exactly why table-aware chunking exists.

## S7: `chunking/adaptive.py`: the router

```python
class AdaptiveChunker(Chunker):
    def __init__(self, config=None, analyzer=None) -> None: ...      # builds both chunkers from the SAME config
    def _split(self, document) -> list[Piece]: ...
```
`_split`: `profile = self.analyzer.analyze(document)`. Pick the structure-aware chunker if `profile.is_structured`, else the recursive one. Return the chosen chunker's `_split(document)`, adding `"chunker": "structure_aware"` or `"chunker": "recursive"` to each piece's metadata, so you can always see which strategy produced a chunk. Optionally log the decision with the module's `logger.debug`.

**Verify:** the fixture routes to `structure_aware` (17 chunks). Each of your small files routes to `recursive` (1 chunk each). Over the whole `data/` folder, which now has 6 unique documents because `handbook.txt` is the long text, expect **22 chunks: 17 + 5**.

## S8: end-to-end

In the script, add a directory mode: run `IngestionPipeline().run(path)` and chunk every `result.documents` entry with the adaptive chunker. Check:
- chunk ids are unique across all documents,
- each chunk's `document_id` belongs to a document in the result,
- `python -m scripts.inspect_chunks data` shows 22 chunks, and the failures and skips you already know about.

---

## Later (each is its own step; we do them in this order)

1. **Token-based sizing.** Override `_length` to count tokens with `tiktoken` (`cl100k_base`, the tokenizer for `text-embedding-3-small`). Aim for roughly 400-512 tokens with 10-20% overlap. At the measured 5.17 chars per token that is about 2,100-2,600 characters, so re-run the script with `--size` in that range and watch the chunk counts fall. The one hard part is the hard-cut fallback: it slices by characters, so with tokens you need to slice by token ids and decode.
2. **Better overlap.** Since 12 of 14 boundaries have none, try carrying the last sentence or two, and compare.
3. **Markdown/HTML/PDF structure.** A real heading hierarchy (`section`, `subsection`) and page numbers. This is where the loaders hand structure to the chunkers.
4. **Table- and code-aware chunking:** protect those blocks from splitting.
5. **Semantic chunking.** It needs embeddings to find topic shifts, so it comes after the embedding step.
6. **Parent-child:** retrieve small chunks, return their larger parent.
7. **Evaluation:** build a small question set over your documents and measure Recall@K for each chunker and size. That is how the numbers above (800/100, 450 tokens) stop being guesses.

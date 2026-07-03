# Editorial conventions — books-validate

You are fixing `chapter.mdx` so it satisfies this project's editorial conventions. The registries
that define what is valid are beside it: `labels.json` (valid `<XRef>` / cross-reference ids),
`references.json` (valid `<Cite>` bibliography keys), and `files.json` (source files and their line
counts, for `<CodeRef>` range checks).

## Rules

1. **`<XRef id="…">`** — the `id` must exist in `labels.json`, **and** must point to the section or
   theorem the surrounding sentence is actually about. A well-formed id that names the *wrong*
   section is still an error; the registry has several similarly-named ids, so read the prose.
2. **`<Cite key="…">`** — the `key` must exist in `references.json`.
3. **Every citation in prose carries a tag.** Wherever the text names a source inline — e.g.
   "Author (YEAR)" or "(Author, YEAR)" — there must be a `<Cite key="…">` for it with the matching
   key. If a citation is written in prose but has no tag, add one.
4. **`<CodeRef path="…" line={N} lineEnd={M}>`** — `path` must be in `files.json`, and `line` /
   `lineEnd` must fall within that file's length.
5. **`<BookLink book="…" to="…">`** — must carry **both** `book=` and `to=`.

## How to work

- Fix tags **in place**. Do **not** add, remove, reorder, or retitle section headings, and do not
  reword prose except to add a required tag (rule 3).
- `python3 validate_fixture.py chapter.mdx` reports structural violations (unknown ids/keys,
  out-of-range CodeRefs, malformed BookLinks) and exits non-zero until they are gone. It is a
  helper, not the whole task: it **cannot** see a well-formed id that points to the wrong section,
  nor a prose citation that is missing its tag. Those you must get right by reading the chapter.

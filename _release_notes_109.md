## v1.0.9 — wrap instead of overflow, and always hand over a file

Two changes, both coming from real drawings that failed to come out.

### Narrow boxes now wrap instead of overflowing

A drawing label is often two Chinese characters wide. `门板` is two characters; its English
rendering `DOOR PANEL` is nine. In a 14pt cell the only way to fit one line is a 2.5pt font —
printed, that is a grey smudge.

v1.0.9 lays every label out in this order:

1. one line, shrinking the font if needed (up to 1.25× the box width);
2. **two lines**, breaking only between words and between CJK characters — `DOOR` is never
   split into `DOO` / `R`. Measured, this lifts the font from 2.5pt to about 4.8pt;
3. no wrap when there is no room above — the second line would sit on the previous one;
4. still does not fit → **keep the source text** and list the spot as untranslated.

The text layer and the OCR layer share one wrapping module (`core/textfit.py`), so two equally
wide boxes on the same drawing are laid out the same way.

Point 4 also closes a worse bug: the old code kept writing after hitting the minimum font size,
producing text twice as wide as the cell, covering the labels to its right. The review caught
it as a blank patch, but only because it read back inside the box — an overflow that happened
to be small enough would have shipped.

### The review no longer withholds the file

v1.0.7/1.0.8 stopped before saving whenever the review was unhappy. In practice the rejected
spots are the ones you cannot do anything about — an 8pt-wide version cell, Chinese characters
inside a stamp — so users reran the file over and over and never received anything.

Now:

```
detect → translate → review ─┬─ pass ─────────────────→ output
                             └─ problems → retranslate → review → …
                                              ↑__ up to 3 rounds __|
                      still unresolved → output anyway, list what is left
```

The file is written, and every unresolved spot goes into the 【未译清单】 text file: page
number, original text, what went wrong. Spots that could not fit keep their original text on
the page — not a blank patch.

Also in this version: short identifiers such as `v1` are recognised and left alone instead of
being translated into a cell too small to hold the result.

### Verification

- `--selftest`: 26 checks, all pass, including two new ones (`text-layer wrapping`,
  `review failure still produces a file`), run against the packaged exe.
- `tools_test_retry.py`: five end-to-end cases — passes first time / rejected then retranslated /
  still failing after three rounds (file produced, list written) / wrapping into two lines /
  cannot fit even wrapped (source kept, no blank patch).
- Two real drawings that previously produced nothing now produce files: a 1-page title-block
  drawing (English → Chinese) and a 2-page fully-outlined drawing handled by OCR (Chinese →
  English).

Upgrade by running the installer over the existing installation — do **not** uninstall first,
uninstalling asks whether to delete your glossary and it will delete your settings along with it.
Your API key and settings are kept by an over-the-top install.

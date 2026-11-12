A review pass before the file is written: nothing goes out unchecked

Three releases in a row shipped a file that looked finished and was not. v1.0.5 fixed rotated
labels that came out blank. v1.0.6 fixed two lines that stayed in Chinese. Both were found by
the customer, on the page, after delivery. The per-item checks in the translation pipeline
could only answer "is this one line usable" — nothing was asking "is this whole document
clean".

This release adds that question, and makes the answer final: if the review fails, no file is
produced.

## What gets checked, and what it catches

| Check | What it looks for | The failure it prevents |
|---|---|---|
| Read-back | The characters written at each replacement site can be read back | A blank patch where text used to be (v1.0.5) |
| Leftovers | A line that was never translated is still on the page | A Chinese sentence in an English drawing (v1.0.6) |
| Coverage | At least something was actually replaced | Shipping the original file, relabelled |
| Semantic spot-check | Converted items, and items whose numbers changed, re-checked by the model | Numbers that add up, meaning that does not |

The first two are the ones that would have caught both previous failures.

**Read-back** is possible only because it happens after writing: erasing is irreversible, so the
only proof that a spot is not blank is to query the page for the characters that should be
there.

**Leftovers** needs a note. On a PDF whose text has been converted to curves — the customer's
file was one — `get_text()` returns nothing at all, because there is no text layer left. The
first version of this check reported "clean" on a page that plainly still had Chinese on it.
The working judgement is the untranslated list: any line that did not get translated is,
by definition, still on the page in its original form. Text-layer read-back is kept as a
second pass, for text the OCR never saw in the first place.

Fragments are not counted: `3mm`, `⌀20`, `A` have nothing to translate, and flagging them would
make the gate impossible to pass.

## Semantic spot-check

Only suspicious items are sent to the model for a second opinion, not every line — a full
re-read would double the cost of every run. An item is suspicious when its set of numbers
changed between source and translation (2.79 m becoming 2790 mm is exactly this case), or when
the source carried a unit and the translation lost it.

The prompt is deliberately narrow: wrong number, wrong unit, changed specification, dropped
content. Style and wording are excluded on purpose — asking for a prose review pulls attention
away from what this pass exists to catch.

Verified against a known-good and a deliberately wrong pair: given four items, three correct
and one saying 200 mm for 2 m, it returned exactly the wrong one and passed the other three.

## One classification change

`verify_metric_output` used to raise a warning when the number of unit symbols in a line
differed between source and translation. That count is extracted by "value immediately
followed by a unit": `50*100*3mm` counts as one, `50.00×100.00×3.00mm` counts as two — correct
on both sides, still flagged. Once these warnings became blocking, a perfectly good line
blocked the whole file on the first real run.

They are now recorded as `notice`, shown in the log as a reference note, and do not block.
Genuine disagreements — a value that does not match the recomputation, a value that vanished —
remain `warn` and still block.

## Blocking, by design

A failed run produces no file, and the log lists each problem with its page:

```
Review failed: 2 items still in the source language

Blocked before writing, no file was produced:
  Page 2 · leftover: 2根5毫米厚镀锌铁板2.79米长 — this line was not translated
  Page 4 · leftover: 2根5毫米厚镀锌铁板2.79米长 — this line was not translated

  No file was produced. Fix the items above and run again.
```

Verified on the customer's drawing in both directions: it passes as-is (40 replacements,
one OCR fragment left untranslated and correctly ignored), and when one line is deliberately
left untranslated it is blocked, on both pages, with the exact text named.

## Also in this release

- `process_pdf` takes `dst_lang` and `reviewer` explicitly. Deriving them from
  `translate_batch.__self__` silently disabled the leftover check when the batch function was
  a wrapper rather than a bound method — the review reported success because it had nothing
  to compare against.
- Packaged self-test is now 22 checks, the new one covering read-back, blank detection and
  leftover detection.

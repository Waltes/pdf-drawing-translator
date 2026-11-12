Numbers rewritten in a different metric unit are no longer rejected as wrong

Reported: two labels stayed in Chinese in the finished drawing — both instances of
"2根5毫米厚镀锌铁板2.79米长" (2 sheets, 5 mm thick galvanised steel plate, 2.79 m long).
Everything around them was translated; only these two stayed in the source language.

The translation was correct. The check that rejected it was not.

When the model was asked to translate that label, it returned:

    2 pcs 5.00 mm thick galvanized steel plate, 2790.00 mm long

2.79 metres written as 2790 mm — the same length, in the unit the rest of the drawing uses.
That is a correct translation, and arguably the better one: every other dimension on this
sheet is in millimetres.

But `_numbers_preserved` verifies a translation by taking every number out of the source and
looking for it, literally, in the output. The source contains 2.79. The output contains
2790.00. The literal search failed, the numeric fallback failed (0.1% tolerance), so the line
was judged "number mismatch" — i.e. the model had miscalculated — and discarded. The batch
retried it twice, the single-item retry threw it away twice more, and the label finally fell
back to the original Chinese with the rest of the page translated around it.

This only hit labels whose length was written in metres. "5根50*100*3mm方管*1103毫米长" came
through fine, because the source was already in millimetres, so 1103 was found verbatim.
Where the source said 1.103米 it failed the same way as 2.79米.

What changed, in `core/translate.py`:

- A number that carries a metric unit is now also accepted when the translation wrote it in a
  different metric unit. "2.79米" is satisfied by "2790 mm", "279 cm" or "2.79 m", because all
  three denote the same length.
- The comparison is made on value **and** unit, not on bare numbers. Accepting bare numbers
  would have let a genuine miscalculation through: 279 mm is also a number that appears when
  you express that quantity in centimetres, so a sloppy 2.79 m → 279 mm would have passed.
  Pairing the value with the unit it is written against keeps that rejection intact.
- Cross-system conversion is untouched. 4-1/8" is still expected as 104.78 mm, and writing
  100 mm is still rejected.

Verified on the reported file (3.05 m guardrail pallet drawing, 5 pages, Chinese to English,
metric):

- Before: 2 labels untranslated, all rejections were "number mismatch".
- After: 40 in-image labels replaced, zero rejections, one item left untranslated — the OCR
  fragment "3mm", which is a piece of a dimension line and has nothing to translate.
- The two reported labels now read "2 pcs galvanized steel plate, 5.00mm thick, 2790.00mm long",
  in place, on both pages 2 and 4.
- Regression checks on the numeric guard: dropped numbers still rejected, 2700 mm for 2.79 m
  still rejected, 4-1/8" → 100 mm still rejected.
- Packaged self-test 21/21, installer 1078 files, no compile warnings.

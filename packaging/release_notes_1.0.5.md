Rotated labels no longer turn into blank patches in image mode

Reported: on an outline-converted drawing, the vertical dimension labels — the ones turned
90° anti-clockwise beside dimension lines, read bottom-to-top — came out as empty space where
they used to be. Not garbled, not translated: blank.

The OCR was fine. Recognition confidence on those four labels was 0.75 to 0.89, and the text
came back complete, including the two long notes at the bottom. The bug was in how they were
written back.

`process_page_images` treated every box as horizontal text:

```python
maxw = r.width + h * 0.2
```

For a vertical label the box is about 13 points wide and 84 points tall. Taking the width as
the available layout length, the English translation — "983.00 mm with Rubber Pad Attached",
around 240 points — had to shrink to fit 16 points. The font came out at roughly 1 point:
thinner than a hairline. The original had already been painted over with the sampled
background colour, so what remained was neither Chinese nor English, just a patch of
background. That is the blank the customer saw.

What changed:

- Vertical boxes are recognised by aspect ratio (height > width × 1.5). For those, the glyph
  height comes from the box width and the layout length from the box height, and the text is
  written back rotated with `Matrix(90)` around the bottom-right corner. That matches the
  drawing-standard direction, so the translation grows upward along the original baseline and
  the glyphs land on top of the box they replace.
- Font size now has a floor: 3.5 points absolute, and no lower than 25% of the original glyph
  height. Below that, the label is skipped, the original stays in place, and it is listed as
  untranslated. A half-erased patch is harder to explain to a customer than an honest miss.
  The floor cannot be set too tight — at 45% it also rejected ordinary cases (a 28pt heading
  legitimately shrinks to 11pt when the translation runs long), and on the test drawing that
  rejected every Chinese label on the page.
- The horizontal path is untouched apart from renaming the height variable.

Verified on the reported file (3.05 m guardrail pallet drawing, 5 pages, Chinese to English,
metric):

- Page 1 through full-page OCR: 38 in-image labels replaced, 3 left untranslated (one OCR
  fragment `3mm`, two where the translation channel failed on a single pass — both reported
  in the untranslated list rather than silently dropped).
- Vertical labels now come out as `dir=(0,-1)` at 5–7pt, legible along the original direction,
  no blank patches. Horizontal notes, dimension figures and the drawing frame are unaffected.
- Packaged self-test 21/21, no failures. Installer packs 1078 files, zero warnings.

One limitation worth stating plainly: when the translation is much longer than the original,
the font shrinks noticeably. That is inherent to writing in place over a fixed box. The
difference now is that it stops shrinking at a readable size and leaves the original alone
instead of producing a smudge.

Install with `PDFTranslator_Setup_1.0.5.exe` below.

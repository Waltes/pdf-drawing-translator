# PDF Drawing Translator User Guide

Version 1.1.3

This guide covers the Windows edition. The program translates engineering drawings (PDF)
between Chinese and English. Text is replaced in place, dimensions are converted to the
unit system you select, and a second copy marks every converted value for review.

Chinese version: [使用说明.md](使用说明.md)

---

## 1 Overview

### 1.1 What it is for

Producing a drawing in another language usually means one of two things: paying a
translation bureau to rebuild it, or editing it by hand in a PDF editor. The first is slow
and expensive; the second misses things. This program takes a third route. It reads the
original, replaces the text where it stands, leaves the geometry alone, and handles the
units at the same time.

### 1.2 Three operations

1. Replace text in place, at the original position, angle, colour and size.
2. Convert dimensions to the selected unit system, recalculating the values rather than
   relabelling them.
3. Mark every converted value in a second copy, so a reviewer can check them one by one.

All three are needed. Replacing text without converting leaves imperial values in a
translated sheet. Converting values without rewriting the unit field produces a document
that contradicts itself: a title block reading inch above a dimension of 38.10 invites an
order for 38.10 inches.

### 1.3 Who it is for

Export sales and merchandising staff, workshops working from foreign drawings, purchasing
departments, engineers reading drawings in another language, and engineering departments
sending Chinese drawings to overseas customers.

---

## 2 Requirements

| Item | Requirement |
|---|---|
| Operating system | Windows 10 or later, 64-bit |
| Disk space | about 75 MB for the installer, about 240 MB installed |
| Memory | 4 GB or more recommended |
| AI endpoint | optional; the program runs without one, with the limits described in 4.1 |
| From source | Python 3.10 or later, python.org build with tcl/tk |

The Microsoft Store build of Python has no tkinter and cannot open the window. If that is
what is installed, the program tries to find another interpreter at start-up; if that
fails, see 3.4.

---

## 3 Installation

### 3.1 Installer (recommended)

Run `PDFTranslator_Setup_1.1.3.exe` and follow the wizard. Shortcuts are created on the
desktop and in the Start menu.

Notes on the install:

- The default is per-user, under `%LOCALAPPDATA%\Programs\PDFDrawingTranslator`. No
  administrator prompt appears.
- Selecting "install for all users" in the wizard switches to `Program Files` and asks for
  elevation.
- A sample drawings folder ships with the install, containing two demo PDFs.
- The executable is named `PDF图纸翻译.exe`; the shortcut name follows the install
  language.
- To upgrade from an older version, just run the new installer into the same location.
  **Do not uninstall the old version first** — uninstalling removes the settings folder
  along with the saved API key. Installing over the top keeps both.

### 3.2 From source

Double-click `启动.bat` in the program folder. The first run performs a dependency check
and prints anything missing in the console.

Install the dependencies with:

```
python -m pip install -r requirements.txt
```

A source install keeps its data in the `data` folder beside the program, not in
`%APPDATA%`. See 3.3.

### 3.3 Data folder and self-test

Configuration, cache and usage records live in the data folder, outside the install
directory. Upgrades therefore keep your glossary, and installing under `Program Files`
cannot break writes.

| Mode | Data folder |
|---|---|
| Installed | `%APPDATA%\PDFDrawingTranslator\data` |
| From source | `data` beside the program |

The folder name is plain ASCII. If you ran an earlier build whose data lived in
`%APPDATA%\PDF图纸翻译`, it is moved over automatically on first launch, together with your
API key and glossary.

The API key is kept in two places. Besides `config.json` inside `data`, an encrypted copy
sits one level up:

| File | Location | Purpose |
|---|---|---|
| `apikey.dat` | `%APPDATA%\PDFDrawingTranslator\` | encrypted copy of the API key |
| `config.bak.json` | `%APPDATA%\PDFDrawingTranslator\` | settings backup taken before an upgrade |

Neither file is inside `data`, so uninstalling cannot remove them. On the first launch
after a reinstall, an empty key in the configuration is filled back in from `apikey.dat`;
if `config.json` is missing entirely, it is restored from `config.bak.json` first. An
overwrite install, an uninstall and reinstall, and a deleted configuration all keep your
key.

`apikey.dat` is encrypted with DPAPI, tied to the current Windows account on this machine.
Moving to another computer or another Windows account requires entering the key again.

To confirm the environment, run the self-test from a command prompt:

```
"%LOCALAPPDATA%\Programs\PDFDrawingTranslator\PDF图纸翻译.exe" --selftest
```

The self-test has 17 checks, covering the data folder, fonts, PDF reading and writing,
both conversion directions, the relation between target language and system language, and
preview rendering. The result appears in a dialog and is written to `selftest.log` in the
data folder. It is offline and costs nothing.

### 3.4 If the program does not start

At start-up the program runs an environment check and prints anything missing, then
stops. Working through that output usually solves the problem. For a separate check, run
from the program folder:

```
python preflight.py
```

It prints one line per item: Python version, tkinter, PyMuPDF, numpy, drag and drop, OCR,
file integrity, data folder writability. A line marked `[×]` is followed by the fix.

If the window really does flash and disappear, the traceback is written to
`启动错误.log` in the program folder. That file is truncated on every successful start.

Other cases:

| Symptom | Cause and fix |
|---|---|
| `app.py was not found` | The launcher was copied elsewhere; it must sit beside `app.py` and the `core` folder |
| Window is very narrow, buttons cut off | HiDPI scaling; restart the program |
| Taskbar icon but no window | The window is behind another one; click the taskbar icon |
| `启动.bat` shows garbled text in an editor | An early build did; the current one contains only ASCII and opens correctly everywhere |

### 3.5 Desktop shortcut (source installs only)

Skip this section if you used the installer; shortcuts are already in place.

A source install runs in place. To add desktop and Start menu entries, run from the
program folder:

```
python tools_install.py
```

The script creates a "PDF 图纸翻译" shortcut in both places, launched through `pythonw.exe`
so no console window appears.

The icon is generated by `tools_make_icon.py`. To change the colours or the artwork, edit
the colour constants at the top of that script (`TOP`, `BOTTOM`, `LINE_HI` and others),
regenerate, then run `tools_install.py` again so Windows reloads the icon. The icon is a
fixed product mark and does not follow the interface theme, so it stays recognisable on
the desktop.

### 3.6 Uninstall

Uninstall from the Windows settings app list. The uninstaller asks whether to delete your
glossary and translation cache. They are kept by default. Choosing to delete removes the
`data` subfolder only: the API key and the pre-upgrade settings backup live one level
above it and always survive, so a reinstall does not ask for the key again.

To remove everything, delete the whole `%APPDATA%\PDFDrawingTranslator` folder after
uninstalling.

---

## 4 Setting up an AI endpoint

### 4.1 Why configure one

The program has four translation channels and tries them in order:

| Order | Channel | Used for | Quality and cost |
|---|---|---|---|
| 1 | Cache | a sentence translated before | identical to last time, no cost |
| 2 | Local glossary | short callouts such as parts, fasteners, fittings | most consistent terms, no cost |
| 3 | AI endpoint | long sentences, technical notes, drawing callouts | best quality, billed per token |
| 4 | Free machine translation | only when no endpoint is configured | fair, daily quota |

Without an endpoint the program still works. Short callouts are reliable, but whole
paragraphs of technical notes go to the free service, which reads poorly, gets terms
wrong, and stops working once the daily quota is gone.

With an endpoint configured, the free channel should stay at zero. The statistics line in
the log confirms it:

```
glossary 55 · AI model 88 · free 0 · cache 3 · kept as-is 9
```

The four numbers are the item counts handled by each channel.

If an endpoint is configured but unreachable, through network failure, exhausted balance
or a local server that is not running, the program keeps those items in the original text
instead of diverting them to the free service. There are two reasons. A machine-translated
line welded into a drawing is worse than an untranslated one, and having chosen an
endpoint, your text should not be sent somewhere else without your knowledge. With the
local provider this matters most: if the server stops, you get no translation, but nothing
leaves the machine.

### 4.2 Steps

1. Click the settings button at the top right of the window and switch to the AI API page.
2. Choose a provider. The base URL and a default model name are filled in automatically.
3. Paste the API key. The list beside the model name offers candidates for the chosen
   provider.
4. Click Test connection.
5. Click Save and close.

A custom endpoint has no preset values; fill in the address and model name from its
documentation. A local ollama or vLLM server uses `http://127.0.0.1:11434/v1`.

If you have no key yet, the bottom of the settings page shows the sign-up page for the
selected provider and the three steps to follow. The instructions change with the
provider, because sign-up flows differ and keys are not interchangeable.

How Test connection decides:

- It first requests the endpoint's model list, which costs nothing.
- If your model name is not in that list, one very short request is sent to confirm, so an
  older working model is not reported as broken.
- If the model answers with nothing at all, which is common with reasoning models, it
  reports failure explicitly. Such a model produces blank pages in a drawing.
- After 45 seconds without a response it reports a network or address problem instead of
  sitting on "working".

Balance query only works where the provider exposes it. DeepSeek, Kimi and SiliconFlow
support it; Qwen and Zhipu do not, and the estimate is then the only guide.

### 4.3 Providers

| Provider | Notes |
|---|---|
| DeepSeek | lowest price, a 7-page drawing costs a fraction of a cent, balance query supported |
| Alibaba Qwen | compatibility mode, fast |
| Moonshot Kimi | balance query supported |
| Zhipu GLM | free models available |
| SiliconFlow | balance query supported |
| OpenAI | needs internet access, uses the system proxy |
| Local model | ollama, LM Studio, vLLM; no cost, no key, needs VRAM |
| Custom | any OpenAI-compatible endpoint |

The key is written to the local configuration file:

- Installed: `%APPDATA%\PDFDrawingTranslator\data\config.json`
- From source: `data/config.json` in the program folder

An encrypted copy is kept one level above the data folder, which is how the key survives
an uninstall. See 3.3. The bottom of the settings window shows the actual path.

### 4.4 Running the model on your own machine

Select Local model (ollama, LM Studio, vLLM). Three things differ from a cloud provider,
and the program handles all three:

1. No key. Leave the field empty. The request goes out without an `Authorization` header,
   which is what ollama expects. There is no need to invent a key.
2. No preset model names. What is available depends on what you have pulled, so a preset
   name would be a guess. Press Test connection instead: the models your server reports
   are listed and the first one is filled in.
3. No cost. The usage line reads "local model, no cost" instead of pricing your own
   hardware at cloud rates.

A one-time setup:

```
ollama pull qwen3:8b        # any model you like
ollama serve                # listens on 127.0.0.1:11434 by default
```

Then select Local model in the settings, leave the key empty, and press Test connection.

Loopback addresses and private ranges (`10.x`, `192.168.x`, `172.16` to `172.31.x`) count
as local and bypass the system proxy. An internal endpoint swallowed by a proxy is the most
common "the server is running but the app cannot reach it" case. A vLLM server on the
company LAN can also go under Custom with an empty key; the rule follows the address, not
the provider name.

Local models are slower. Set items per batch to about 10 so one slow response does not
stall a whole batch.

### 4.5 Cost and usage

- The status strip at the top of the main window shows the model, the account balance, and
  the tokens and estimated cost of the current run.
- After each file, the log records this run's usage and the running total.
- Prices are approximations of published rates, for orientation only. The provider's
  invoice is authoritative.
- Prices can be edited in `data/prices.json` as `{"model": [input_price, output_price]}`,
  in currency units per million tokens.

For reference, a 7-page drawing with 150 callouts uses about 40k input tokens and 6k output
tokens, roughly ¥0.12 on DeepSeek. A second run of the same file is mostly served from
cache.

### 4.6 What is sent over the network

Only the text that has to be translated. The PDF itself, its images, its layout, its file
name and its folder path never leave the machine.

| Step | Sent | Not sent |
|---|---|---|
| PDF parsing and writing | nothing; runs locally | the file |
| OCR for scans | nothing; the model ships with the program and runs locally | the scanned image |
| Unit conversion, arithmetic checks, marked copy, preview | nothing; all local | |
| AI endpoint, cloud | each item's text, the target language, matched glossary entries, a fixed system prompt | file name, page number, coordinates, images |
| AI endpoint, local | the same fields, to a local address | |
| Free fallback (MyMemory) | one item's text, truncated to 480 characters, plus the language pair | |

Two items deserve separate mention:

- Glossary hits. Entries from your `glossary.csv` that actually appear in the batch are
  sent as hints, at most 60. Entries that do not appear are not sent.
- Title block text. Company names, customer names and project names are text like any
  other, so whatever needs translating there is sent. The most identifying part of a
  drawing is often the first part to leave.

With no endpoint configured, the free fallback is used, and it is a third-party public
service. Free does not mean private. If a cloud endpoint fails part-way through, the
remaining items keep their original text rather than being pushed to the free service, so
one drawing is never split across two providers.

Two local files are worth knowing about:

- `%APPDATA%\PDFDrawingTranslator\data\cache.json` holds source and translation pairs in
  plain text, up to 20,000 entries. It is a readable copy of your text; delete it before
  handing the machine to someone else.
- `config.json` holds the API key in plain text. The encrypted copy one level up at
  `apikey.dat` holds the same key readable only by this Windows account on this machine.

To keep everything on one machine, select the local provider. The only traffic then is
loopback.

---

## 5 Translating and output

### 5.1 Steps

1. Drag PDF files onto the window, or click the drop area to select them. Several files can
   be queued and are processed in order.
2. Choose the source language, the target language and the output units. See section 6.
3. Press Start.

During the run the log pane at the bottom of the window reports progress and statistics.
When it finishes you can open the output folder from there.

The "files to translate" list holds exactly what this run will process. Two rules:

- **Dropping replaces the list.** Whatever you drop is what gets translated; anything
  that was queued is taken off the list. Nothing on disk is touched. Add those files
  back with the file picker if you still need them.
- **Finished files leave the list.** Each file is removed as soon as its run completes,
  so the list always shows what is still pending. Files that were skipped or failed stay
  on the list so you can retry them after adjusting the settings.

That means you can drop the next drawing straight in without the previous one being
translated a second time. The file picker appends instead, which is handy for building
up a batch before pressing Start.

### 5.2 Output files

Each PDF produces its own folder, named after the file:

```
BR-01-Fence-Brace/
├── BR-01-Fence-Brace.pdf                 copy of the source
├── [Translated] BR-01-Fence-Brace.pdf    the working file
├── [Marked] BR-01-Fence-Brace.pdf        converted values highlighted
├── [Untranslated] BR-01-Fence-Brace.txt  items not translated, with page numbers
├── [Preview] BR-01-Fence-Brace.png       first page as an image
└── [Data check] ....txt                  only when a converted value could not be verified
```

Notes:

- The folder is created beside the source file by default. The target can be changed with
  the folder selector.
- Name collisions get a `_1`, `_2` suffix. Neither the source file nor an earlier run is
  overwritten, so results from different settings can be compared.
- If a file contains no imperial values to convert, for example a metric drawing, no marked
  copy is produced, and the completion message says so.
- The prefixes follow the interface language. With the interface in Chinese they become
  `【翻译】`, `【标注】`, `【未译清单】`, `【预览】` and `【数据校验清单】`, never a mix.
- The untranslated list is always produced. When nothing is missing it says so.

### 5.3 What the marked copy is for

The marked copy is for whoever checks the drawing. A value of `38.10mm` was computed by the
program; whether it corresponds to the original `1-1/2"` is not something the eye can
confirm. Once marked, each converted value can be checked against the original.

Values that were not converted are not marked, for example `11 ga`, `38°` and quantity
columns. What was changed, and what was not, is visible at a glance.

### 5.4 Coverage and the untranslated list

After a conversion the log contains a coverage block:

```
Text coverage: 150 to translate, 114 translated (5 from bitmaps), 36 untranslated
  untranslated p.3: 46° · collar at 87-3/4", foot 78-1/2" out
  …24 more, see [Untranslated] <name>.txt in the folder
```

- To translate, translated and untranslated are counted item by item, not estimated.
- Drawing numbers (`BR-01`), revision letters (`E`) and pure model codes (`DN50`) are not
  counted as untranslated, because they are not meant to be translated.
- Reasons for missing items include busy backgrounds that were not repainted, sentences
  that failed the checks, and OCR failures on scans. See section 11.

#### Outlined text: visible on the drawing, invisible to the text layer

Some drawings are exported with "convert text to outlines". Part of the lettering is drawn
as vector outlines — zoom in and the characters are hollow, stroked but not filled. They are
plainly visible on the page, yet absent from the text layer, so the normal pipeline cannot
reach them.

The program reads them, matches them against the glossary, and rewrites the ones it can:

- A match (`Front view`, `Top view`, `Side view`, and section headings such as
  `Cut B - B`) is **replaced with Chinese in place**. The type size follows the measured
  height of the original lettering and the colour is carried over. The log and the closing
  summary both state how many were replaced.
- No match stays as it is and is listed in the untranslated list, with the reason.

Two cases are deliberately left untouched:

- The recognised wording is not in the glossary — the program will not write a word it is
  not sure about.
- A dimension line runs through the box — covering it would break the line.

Matching against the glossary is what makes this safe. Hollow outlines OCR unreliably:
`Top view` comes back as `Top vlew`, `Side view` as `Sldo vow`. Handing that straight to a
translation model produces a different word on the drawing, and a wrong word is far harder
to notice than leftover English. Glossary matching with a small tolerance for misread
letters fixes the misreads without inventing anything.

Whatever could not be changed is still listed in its own section of the untranslated list:

```
Outlined text: these characters were converted to vector outlines on export.
They are visible on the drawing but absent from the text layer.
Where the wording matched the glossary, the program replaced it with Chinese in place;
the items below could not be changed and remain in the original language.

page 1  some part label
```

For a complete fix, have the drafter re-export a PDF with live text, or run the file through
image mode, which redoes the whole page (limited: type gets compressed).

#### When the box is too narrow: wrap first, keep the source as a last resort

There is an unavoidable conflict in drawings: 「门板」is two Chinese characters, its English
rendering `DOOR PANEL` is nine, and the cell is fourteen points wide. Forcing it in means a
2.5pt font — printed, that is a grey smudge, which is the same as not writing anything.

So the program tries these in order, moving on only when the previous one fails:

1. **One line fits** → write one line, shrinking the font a little if needed (up to 1.25×
   the box width; drawing labels usually have whitespace around them).
2. **One line does not fit** → **wrap into two lines**. Breaks only between words and between
   CJK characters, never inside a word (`DOOR` is never split into `DOO` / `R`). Measured, this
   lifts the font from 2.5pt to about 4.8pt, which is readable.
3. **No room above for a second line** → do not wrap. The second line would sit on top of the
   line above, which is harder to notice than a missing translation.
4. **Still does not fit after wrapping** (a box only 8pt wide, say) → **keep the source text**
   and record the item in the untranslated list.

Point 4 is the important one: **the program never writes an overflowing translation**. The
earlier version kept writing after hitting the minimum font size, producing text twice as wide
as the cell and covering the labels to its right — far worse than "this spot is not translated".

The text layer (vector text) and text inside images (OCR) share the same wrapping rules, so two
equally wide boxes on one drawing are laid out the same way.

### 5.5 Review before the file is written, and automatic retranslation

After the translations are written back and before the file is saved, the whole document is
checked once more. The full flow is:

```
detect → translate → review ─┬─ pass ─────────────────→ output
                             └─ problems → retranslate → review → …
                                              ↑__ up to 3 rounds __|
                      still failing after 3 rounds → output anyway, list what is left
```

**A failed review does not simply give up — the offending items are sent back for
retranslation**, and the document is reviewed again afterwards.

**After three rounds the file is produced anyway.** Every spot that could not be fixed is
written into the untranslated list, so you can decide whether the drawing is good enough.
The earlier behaviour was "no file when the review fails", which turned out to be worse in
practice: the rejected spots are exactly the ones you cannot do anything about (a version
cell 8pt wide, Chinese characters inside a stamp), so you reran the file over and over and
only ever got "fix the items above and run again".

Six things are checked:

| Check | What it looks for | What you get if it is skipped |
|---|---|---|
| Read-back | Are the written characters actually legible at that spot | A blank patch where text used to be |
| Leftovers | A whole line still in the source language | Your customer sees a foreign sentence |
| Coverage | Nothing at all was replaced | You ship the original file by accident |
| Number re-check | Do converted values match a fresh calculation from the source | Wrong dimensions, wrongly cut parts |
| Number count | Does the count of values in a line match the source | One dimension merged away or lost |
| Semantic spot-check | Items that were converted or whose numbers changed, re-checked by the model | Numbers right, meaning wrong |

The spot-check covers only suspicious items, not every line, so it does not noticeably add
to the run time or the cost.

#### When the number count does not match

Two dimensions in the source, one left in the translation — usually a value was merged or
dropped. This used to be a mere hint that never blocked anything; in practice a source with
two dimensions and a translation with three numbers was reported as "review passed", which
lets a file with a genuinely missing dimension through.

The rule now:

1. The item is **sent back for one retranslation** — most of the time the second attempt is right.
2. If the count still differs, the file is **not marked as failed** (the count is taken from
   unit symbols, so an unusual but correct rendering can also mismatch), but the spot is listed
   in a separate paragraph: "the number of values here does not match the source — please
   compare against the original".

#### Only the failing items are retranslated

Not the whole document. Rerunning everything means paying the model for lines that were
already right, doubling both time and cost. Instead each rejected item is sent back
**together with the reason it failed**:

| Problem | What the model is told | What it should change |
|---|---|---|
| Not translated | last attempt returned the source text | translate it, keep numbers and units |
| Wrong number | the value a fresh calculation gives | follow the source, do not alter the dimension |
| Does not fit | the box is too narrow, the text would shrink to unreadable | give a shorter rendering, use standard abbreviations |
| Semantically suspect | what the spot-check objected to | translate from the source, drop nothing |

From round 2 onward the request adds "the previous attempt with the same instruction still
failed, so change the wording". This stops the model from returning the same sentence forever.

The limit is 3 rounds (the first review plus two reviews after retranslation). At that point
the program stops rather than keep spending API budget.

#### Stamps and watermarks are reported but allowed through

Drawings often contain content that cannot be translated: stamps, watermarks, text inside the
title block. The rule for telling them apart is a single one — **can it be located as an
erasable text box?**

- Located: treated as an error, sent back for retranslation.
- Not located: treated as untranslatable content. It cannot be replaced in place, so
  **the file is still produced** and the items are listed separately in the log for you to
  accept.

Blocking on those would mean a drawing with a stamp could never be produced at all. So they
inform but do not block:

```
Review passed: 40 writes verified · 5 pages of text layer · 2 semantic checks · 2 items could not be located
Review rounds: round 1: 3 problems, 3 retranslatable, 3 written → round 2: passed

The following were left in place as untranslatable content (stamps, watermarks, text inside graphics):
  Page 1 · leftover: 公司图签  — still in the source language (no replaceable text box found)
  Use the file as is if these are expected; if any of them should be translated, fix the source and rerun.
```

#### When it genuinely cannot be fixed

```
Review result: round 3 failed (file produced anyway): 2 items still in the source language
Review rounds: round 1: 3 problems, 3 retranslatable, 3 written → round 2: 2 problems, 2 retranslatable, 0 written

The file was produced, but these spots were not handled — check them against the source:
  Page 2 · leftover: 2根5毫米厚镀锌铁板2.79米长  — this line was not translated
  Page 4 · leftover: 2根5毫米厚镀锌铁板2.79米长  — this line was not translated
```

**The file is in the folder, and those two spots still show the source text** — not a blank
patch. The same list is written into the 【未译清单】 text file, so you can print it and go
through the drawing spot by spot.

A second, non-blocking category — reference notes — appears in the log without stopping the
run. Example: the number of units in a line does not match. That check counts unit symbols,
and correct translations are often flagged by it, so it informs but does not block.

### 5.6 Review reminder

Every run ends with an orange reminder at the bottom of the log:

```
Check every translated line and converted value before you use this: machine translation
and automatic conversion can both be wrong, and quoting, ordering or machining straight
from the output can cost you real money.
```

This is not boilerplate. A term translated the wrong way round, or a tolerance rounded off,
becomes real money once it reaches a quotation or a machining order. Go through the output
before sending it on, especially dimensions, tolerances, materials and surface treatment.

---

## 6 Direction and output units

### 6.1 The three selectors

All three sit in the same card on the main window. All three are remembered between runs.

| Selector | Options | Notes |
|---|---|---|
| Source language | Auto-detect, Chinese, English, Japanese, Korean, German, French, Spanish, Portuguese, Russian, Italian | Auto-detect classifies each block; blocks already in the target language are skipped |
| Target language | Auto (your system language), or a specific language | Auto means the language Windows is set to, spelled out in the label, for example Auto (Japanese). An English drawing on a Japanese machine comes back in Japanese; the same drawing on a Chinese machine comes back in Chinese. Blocks already in the target language are left alone |
| Output units | Metric (mm, kg), Imperial (in, lb), No conversion | Independent of the language pair |

Three things to keep in mind:

- The target list never offers the same language as the source. "English into English" has
  no meaning.
- Translation and unit conversion are independent. An English drawing with metric output,
  and a Chinese drawing with imperial output, are both normal uses.
- No conversion also suppresses the marked copy. A file with yellow highlights over
  unchanged numbers is more misleading than no extra file.

The built-in glossary covers Chinese and English only. The other eight target languages
need an AI endpoint or the free fallback, so terminology quality depends on the model.
Configure an endpoint before translating into those languages.

### 6.2 Conversion rules

Imperial to metric, recognised forms:

| Form | Origin | Result |
|---|---|---|
| `4"` | straight double quote, native CAD output | `101.60mm` |
| `4″` | typographic inch sign | `101.60mm` |
| `4”` `4“` | full-width quotes produced by Word and InDesign | `101.60mm` |
| `4''` | two single quotes, common in older DXF exports | `101.60mm` |
| `4 in.` `4in` `4 IN` | abbreviation | `101.60mm` |
| `1-1/2"` | mixed fraction | `38.10mm` |
| `1 1/2"` | mixed fraction, space separated | `38.10mm` |
| `1 - 1/2"` | spaces around the hyphen, from justified text | `38.10mm` |
| `8'` `8’` `8′` | single quote, full-width, prime sign; feet | `2438.40mm` |
| `2'-0"` `2'-0''` | feet and inches written together | `609.60mm` |
| `9 ft` `9英尺` | feet, abbreviated and in Chinese | `2743.20mm` |
| `25 lb` `8 oz` | pounds and ounces | `11.34kg` `0.227kg` |

One case needs a separate word. A single `'` means feet; two `''` mean inches. Reading `4''`
as feet gives `1219.20mm`, twelve times too large, enough to ruin a batch of material. The
order in which the two symbols are matched is therefore fixed.

Formatting rules:

- Lengths are written in millimetres with two decimals, and trailing zeros are kept: `1"`
  becomes `25.40mm`.
- Mass is written in kilograms, never grams. At or above 1 kg, two decimals: `25 lb`
  becomes `11.34kg`. Below 1 kg, three decimals: `8 oz` becomes `0.227kg`, `1 oz` becomes
  `0.028kg`.

The extra digit below 1 kg exists because with two decimals 1 ounce would read `0.03kg`:
28.35 grams expressed as a 30 gram quantity. On a scale in a hardware bag, that difference
is real. The rule affects nothing above 1 kg.

The number of decimals was chosen deliberately. Drawing values have to line up with
tolerances and inspection standards; a file containing both `104.8` and `104.78` will be
read as two precision grades at inspection.

Two categories are never converted, because they are designations rather than lengths:

- Thread and fastener specifications: `1/4"-20`, `M8×30`, `NPT 1/2"`, `BSP 3/4"`
- Standard numbers, drawing numbers and model codes: `ANSI`, `ASTM`, `Rev E`, `DN50`,
  `SCH40`

Replacing `NPT 1/2"` with `12.7mm` means purchasing buys the wrong part.

In Chinese drawings, 寸 applied to pipe is a colloquial name for nominal diameter and is
converted to DN25, DN50, DN100 and so on.

With imperial output, or when a Chinese drawing is translated into English, metric values
become fractional inches, because a workshop reads a tape measure: `38.10mm` becomes
`1-1/2"`, `8.73mm` becomes `11/32"`. Values that no denominator up to 64 divides evenly
fall back to decimals: `2000mm` becomes `78.74"`, never `78-47/64"`. Small tolerances keep
their significant digits: `0.05mm` becomes `0.002"`, never `0"`.

With no conversion, no value changes and no marked copy is produced.

### 6.3 The unit field

A title block normally has a cell stating the unit, for example `UNITS / inch` or a Chinese
equivalent. Once the dimensions are in millimetres, leaving that cell as inch causes
misreading: a workshop seeing inch above `38.10` orders 38.10 inches, when what is needed
is 38.10 millimetres, twenty-five times less.

The cell is rewritten over two lines:

```
UNITS
mm
converted from inch
```

The first line carries the new unit; the line below names the original unit and states that
conversion happened. The cell is usually only a few dozen points wide, and a single line
would shrink below legibility, hence two lines.

This only happens in files where a conversion actually took place. A drawing that was
already metric keeps its unit field untouched.

---

## 7 Interface and settings

### 7.1 Appearance

Click the settings button at the top right and switch to the Appearance page.

| Item | Notes |
|---|---|
| Interface language | Chinese, English. Applies immediately and rebuilds the window. Language names are always written in their own language, so you can find your way back out of an interface you cannot read. On first run it follows the system language: Chinese Windows gives Chinese, anything else gives English. These are the only two interface languages; translation targets are a separate list of ten, see section 6 |
| Theme | seven palettes: Deep Space, Midnight Violet, Ice Cyan, Pine Green, Warm Sand, Graphite, Morning Mist. All surfaces, borders, buttons and accents change together |
| Font | system fonts usable as a UI font. Vertical `@` families and symbol fonts are filtered out |
| Size | 8 to 16, default 10, applied to the whole interface |
| Window opacity | opaque, slight, medium, clear. The settings window stays opaque because it sits over the main window and two layers of text do not read |
| Completion sound | soft, system, silent, previewed on selection |

Themes and opacity apply on click and persist automatically. Font and size are written by
Save and close. After saving, the window repaints without a restart. Restore defaults
reverts everything.

The completion sound is a synthesised two-note chime, not the Windows warning sound. The
audio file is generated to `data/done.wav` on first playback and read from there afterwards.

### 7.2 Window controls

The window follows a macOS style:

- The three dots at the top left are close, minimise and maximise, with symbols shown on
  hover.
- Drag the title bar to move the window; drag an edge to resize.
- A minimised window goes to the taskbar; click the icon to restore it.

### 7.3 About the glass effect

Real acrylic blur washes out every card in a Tk window and destroys the dark hierarchy, so
it is off by default. The frosted look comes from layered greys, one-pixel borders and
shadows. For transparency, use the window opacity setting, which applies to the whole
window and keeps both appearance and legibility under control.

---

## 8 Reading the run information

The status strip at the top of the main window looks like this:

```
AI API: enabled · DeepSeek · deepseek-chat ｜ balance ¥87.65
This run: 69 requests · input 42,492 / output 5,639 tokens (total 48,131) · est. ¥0.1188
```

The first line reports endpoint state and balance; the second reports calls and estimated
cost for the current run. With no endpoint configured, the first line says so.

The coverage block in the log is the main signal for whether a translation is complete; see
5.4.

---

## 9 How the result is controlled

Six mechanisms decide how good the output is.

### 9.1 Paragraphs are rebuilt before translating

A paragraph of notes is usually one text object per line in the PDF. Translating line by
line yields disconnected fragments. The program joins the lines of one paragraph into a
sentence, translates it, then breaks the result to the original line widths. The text reads
naturally and nothing moves.

### 9.2 Numbers are verified

Digits in the source (`38°`, `1-1/2"`, `M8×30`) must appear in the translation, or the item
is rejected and retried. On a drawing a digit is the specification.

Lines that went through unit conversion are the exception, since their numbers changed. For
those, the expected metric value is computed with the same rules and compared; a model
writing `100mm` for `4-1/8"` is still caught.

### 9.3 Converted values are recomputed

Conversion works from unit symbols still present in the text, so it cannot see a case where
the model already wrote `100mm`. The unit is then metric, the rule considers the value
converted, and nothing else looks at it. The number is wrong and no one notices.

An exit pass therefore recomputes from the source with the same rules and compares each
expected value with the metric value in the translation.

| Case | Action |
|---|---|
| Agrees within 0.5% or 0.05 mm | accepted |
| Disagrees, same count of values per unit | replaced with the computed value, logged as corrected |
| Disagrees, counts differ | warning only, number unchanged, logged as needs checking |
| A convertible value is still imperial | converted, logged as corrected |

Values are never rewritten when the counts do not line up. With `3-3/4" × 1-1/2"` reduced
to a single `95.25mm`, changing either number is a guess, and a wrong guess is worse than a
flag. Results appear in the status strip and in the data check list in the output folder.
Each entry carries the page, the source text and the translation, marked as either
corrected, which needs a glance, or needs checking, which needs a comparison against the
original.

### 9.4 Half-translated text is rejected

`Padlock孔` or `Why this锁紧环` is worse than an untranslated line: the sheet becomes
unreadable. Any leftover English word outside the whitelist fails the item, which is then
retried on the next channel down.

### 9.5 Annotations inside images are handled

Some drawings rasterise part numbers into 3D renders, where no text layer exists. Those are
located with OCR, the surrounding background colour is sampled and filled, and the
translation is drawn in the original colour. A flat white patch would be visible at once on
a grey object.

Safety boundary: busy backgrounds, patterns and strong textures are left untouched rather
than repainted, and listed as untranslated.

Rotated labels (text turned 90° beside dimension lines) are written back in their original
direction rather than flattened to horizontal. Laid out horizontally inside a box ten points
wide, the text collapses to an unreadable smear that reads as a blank patch. When the
translation is longer than the original, the font shrinks; if it would drop below a quarter
of the original height, that one is skipped, the original is left in place, and it is listed
as untranslated — a missing translation is easier to explain than a white smudge.

---

## 10 Glossary and corrections

| File | Purpose |
|---|---|
| `data/glossary.csv` | glossary, both directions, one entry per line as `chinese,english,category` |
| `data/fixes.csv` | correction table for recurring machine-translation errors, as `wrong,right` |

- Changes take effect immediately; the cache is invalidated automatically.
- Glossary entries are sent to the model as reference terms, and terminology stays
  consistent within one drawing.
- Short callouts, six English words or fewer, go through the glossary first and cost
  nothing.

---

## 11 Known limits

| Case | Explanation |
|---|---|
| Text converted to outlines | some CAD exports draw text as graphics, so the file contains no editable text. The program detects this and skips the file by default instead of writing a translation identical to the original. Image mode recognises each page and writes over the original, but font sizes get compressed and some labels may cover drawing lines. The clean fix is a PDF re-exported with text kept as text |
| Fully scanned drawings | no text layer, OCR only, limited accuracy; flagged when the run starts |
| Small text over dense artwork | not repainted, kept as original and listed as untranslated |
| Chinese longer than English | the font shrinks to fit, down to a floor, never past legibility |
| Copyright and footer lines | long strings with names or brands are sometimes kept; normal |
| Conversion without an endpoint | `4"`, `4”`, `4''`, `4 in.`, `1-1/2"`, `1 1/2"`, `inch`, `ft`, `lb`, `oz`, `9英尺`, `25磅` are all converted; a bare lowercase `in` preceded by a space, as in `12 in`, is not recognised, because `in` is too often a preposition |
| Uncertain conversions | only one-to-one matches are corrected; anything else is reported as needs checking |

---

## 12 FAQ

Q. The log says the AI API call failed.

Open the settings and press Test connection. The result distinguishes a key error (401), an
exhausted balance (402), a wrong address or model name (404), and a network failure.

Q. The log says the free online translation quota is exhausted.

That is the MyMemory daily limit, used only when no AI endpoint is configured. If you do
have an endpoint configured and still see it, the AI channel passed nothing, so test the
connection first.

Q. The drawing is still English after converting.

Look at the coverage block. Untranslated entries hint at the cause: a whole line still in
English failed the checks, usually a dropped digit or leftover English; a partially
translated line sits in a busy area covered under the limits.

Q. Can it process several files at once?

Yes. Drop several files in and they are processed in order, with a combined summary of
usage and coverage at the end.

Q. Can I use a model running on my own machine?

Yes. Select Local model (ollama, LM Studio, vLLM), leave the key empty and press Test
connection. The available models are read back automatically. See 4.4.

Q. Do my drawings leave the machine?

The files do not. Only the text that needs translating is sent, and only when a cloud
endpoint is configured. With the local provider everything stays on the machine. The
item-by-item table is in 4.6.

---

## 13 Moving and sharing

To share, send `PDFTranslator_Setup_1.1.3.exe`, about 75 MB. The recipient installs it and
is ready to use. No API key is bundled; the recipient supplies one, or works with the
glossary and the free fallback.

To move to another machine, install first, then copy `%APPDATA%\PDFDrawingTranslator\data`
from the old machine to the same path on the new one. Your glossary (`glossary.csv`) and
API key (`config.json`) come along. If the old machine runs an earlier build, its folder is
`%APPDATA%\PDF图纸翻译\data`; copy its contents into `%APPDATA%\PDFDrawingTranslator\data` on
the new machine, the files are the same.

For a source install, copy the whole `pdf_drawing_translator` folder, install Python, and
run the launcher. Re-run `python tools_install.py` on the new machine, because shortcuts
store absolute paths.

Each person keeps their own key on their own machine. That is why endpoint configuration is
left to the user.

---

## 14 License

Released under the MIT license. See `LICENSE`.

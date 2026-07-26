# Sample PDFs

A small set of example documents for trying out `pdf-darkmode`. Each one pushes
a different part of the converter, and **every file here is free to
redistribute**. It's all public-domain content, with sources listed at the
bottom.

Try any of them:

```bash
python ../pdf_darkmode.py 01-classic-novel.pdf --mode text --theme sepia
python ../pdf_darkmode.py 02-illustrated-report.pdf --theme warm
python ../pdf_darkmode.py 03-scanned-notes.pdf --images invert
```

| File | What it tests |
|------|---------------|
| `01-classic-novel.pdf` | Long body text, page after page. Clean, readable dark mode at book length. |
| `02-illustrated-report.pdf` | A colored heading, charts, a table and a photo on one page. Hue preservation, line-art inversion and photo restoration all at once. |
| `03-scanned-notes.pdf` | An image-only page with no text layer. Proves the converter works on scans, where "select all and invert" does nothing. |

## Before and after

The [`dark/`](dark/) folder has the converted output for each sample, so you can
see the result without running anything:

| Input (light) | Output (dark) | Command |
|---------------|---------------|---------|
| `01-classic-novel.pdf` | `dark/01-classic-novel-dark.pdf` | `--mode text --theme sepia` |
| `02-illustrated-report.pdf` | `dark/02-illustrated-report-dark.pdf` | `--mode text --theme warm` |
| `03-scanned-notes.pdf` | `dark/03-scanned-notes-dark.pdf` | `--mode text --theme charcoal --images invert` |

The first two were done in text mode, so their text is still selectable and
searchable and the files stay around 75 KB. The scanned notes have no text
layer, so that one falls back to image mode and gets rasterized. Re-run any
command above to regenerate the matching output.

## Sources and licenses

- **`01-classic-novel.pdf`**: text of *Pride and Prejudice* by Jane Austen
  (first published 1813, public domain), from
  [Project Gutenberg](https://www.gutenberg.org/ebooks/1342) and re-typeset for
  this repo. The Project Gutenberg header and footer were stripped, leaving only
  the public-domain novel text.
- **`02-illustrated-report.pdf`**: original text and charts written for this
  repo (released under the repo's MIT license). The photo is *The Blue Marble*,
  Earth seen from Apollo 17 (NASA, 1972), a U.S. Government work in the public
  domain, via
  [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:The_Earth_seen_from_Apollo_17.jpg).
- **`03-scanned-notes.pdf`**: original text written for this repo, rendered to a
  scan-like image with no text layer.

None of this is under copyright, so these samples can be committed,
redistributed and used in demos with no attribution required. The credits above
are just good manners.

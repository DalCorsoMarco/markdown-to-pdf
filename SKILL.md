---
name: markdown-to-pdf
description: Generates a real, laid-out PDF from Markdown content, using ONLY a deterministic Python script (mistune + reportlab) -- no per-request reportlab scripting, no token cost beyond running the command. IMPORTANT: use this proactively any time you're about to produce a .pdf and your instinct is to write a one-off Python script calling reportlab (or any other PDF library) directly -- write the content as Markdown instead and run this script on it. Headings map to real Title/Heading1-6 paragraph styles, bullet/numbered lists become real nested list layout, tables become real laid-out tables, [text](url) becomes a real clickable link, bold/italic/strikethrough/code become real text formatting, and ![alt](path) embeds the real image (resolved relative to the Markdown file's own directory), automatically paginating across pages via reportlab's own layout engine -- not hand-positioned text. This is the fourth and last of this family's write-direction skills (siblings: markdown-to-docx, markdown-to-pptx, markdown-to-xlsx), and the odd one out: unlike those three, a PDF is a rendered/paginated format, not an editable structured one, so there's no lossless round trip back through pdf-to-markdown to expect -- pdf-to-markdown's own heuristics (tuned for real-world scanned/printed documents) can misread a script-generated PDF's specific layout quirks even when the PDF itself renders correctly. If the user needs a document built from a template, or wants pixel-precise print design, that's a different job than generating a fresh PDF from content.
---

# Markdown to PDF (script-only, zero token cost)

## The point of this skill

Asked to produce a PDF, a model without a tool for it typically
improvises a one-off script -- calling `reportlab` or a similar library
directly, working out styles, pagination, and layout from scratch every
time. This skill inverts that: the model writes Markdown, and
`scripts/markdown_to_pdf.py` builds the actual PDF using reportlab's
Platypus layout engine, which handles pagination, page breaks, and text
wrapping automatically -- the alternative (hand-positioning text at fixed
coordinates) would just reinvent a worse version of exactly that.

## The odd one out in this family

`markdown-to-docx`, `markdown-to-pptx`, and `markdown-to-xlsx` all write
*editable, structured* formats -- their own read-direction siblings can
losslessly reconstruct headings, lists, tables, and more from what they
generate. A PDF is different: it's a *rendered, paginated* format with no
equivalent structured document model underneath once it's built. That
means:

- **There's no round trip to verify against `pdf-to-markdown`** the way
  the other three skills verify against their own reading counterpart.
  During development, a generated PDF was confirmed correct by rendering
  it to an image and inspecting it directly (via PyMuPDF) -- not by
  reading it back through `pdf-to-markdown`, whose column/font-size
  heuristics (tuned for real-world scanned and printed documents) can
  misread a script-generated PDF's specific layout even when the PDF
  itself is completely correct. If you ever want to sanity-check a
  generated PDF's *content* programmatically, render it to an image and
  look, rather than trusting `pdf-to-markdown`'s heuristic read of it.
- **Every element is either a real styled/laid-out flowable or it isn't
  in the PDF at all** -- there's no intermediate "kind of structured"
  state the way an oddly-formatted Word list still carries real
  `numPr` data underneath.

## Usage

**Single file:**
```bash
python3 scripts/markdown_to_pdf.py <input.md> [output.pdf] [--force]
```

**A whole folder:**
```bash
python3 scripts/markdown_to_pdf.py <folder> [output_folder] [--recursive] [--force] [--jobs N]
```

## What each Markdown construct becomes

- The first `#` becomes the "Title" style; every other heading maps to
  `Heading1`–`Heading6` (reportlab's default stylesheet defines exactly
  six, matching Markdown's own range).
- `**bold**`, `*italic*`, `` `code` ``, `~~strikethrough~~`, and
  `[text](url)` become real formatted, clickable text.
- Bullet/ordered lists become real nested list layout with correct
  indentation per level.
- Tables become real laid-out tables, each cell's text wrapped in its own
  paragraph so long content wraps instead of overflowing.
- `![alt](path)` embeds the real image (resolved relative to the
  Markdown file's own directory), scaled to fit the page width while
  preserving its aspect ratio -- and automatically flows to the next page
  if it doesn't fit the remaining space on the current one, exactly like
  any other flowable.
- `> quoted text` becomes an indented, italicized block.
- A thematic break (`---`) becomes a real horizontal rule line, not a
  page break.
- A fenced code block becomes a monospaced, whitespace-preserving block.

## The skill's memory: CRC32, read back via PyMuPDF

reportlab can *write* PDF metadata but has no API to *read* it back, so
this script reuses `pdf-to-markdown`'s own dependency, PyMuPDF (`fitz`),
purely for that read-back step: the source Markdown's path and CRC32 are
stamped into the generated PDF's Subject metadata field, and checked via
`fitz` on the next run. Re-running on unchanged Markdown is an instant
no-op; `--force` regenerates anyway.

```bash
python3 scripts/markdown_to_pdf.py --list-cache <folder> [--recursive]
```

Also exposed as `/markdown-to-pdf:list-cache`.

## How it works, and where it can be wrong

**All inline text is escaped before reportlab's markup is applied.**
reportlab's `Paragraph` text is itself a small XML dialect -- a literal
`&`, `<`, or `>` in Markdown content has to be escaped before any
`<b>`/`<i>`/`<a>` tags are wrapped around it, or reportlab's parser breaks
on the content itself. This was verified directly during development with
a literal `&` in the source Markdown.

**Images can't sit inline within a paragraph's text flow** -- reportlab's
Paragraph markup has no inline-image support, so an image is placed as
its own flowable immediately after the paragraph or heading it appeared
under, not interleaved exactly where it sat in the source text.

**Table cells don't recurse into nested tables**, and a table's own
column widths are determined automatically by reportlab from content,
not from any width hints in the Markdown (which doesn't have any).

**Formatting nested inside a hyperlink's own text isn't reconstructed**
-- `[**bold**](url)` becomes a plain (if still clickable) link reading
"bold", the same scope cut as this family's other write-direction skills.

## Default styling

reportlab's stock stylesheet is entirely black text with minimal spacing
— correct, but it reads as a raw dump. `build_styles()` replaces that
with a restrained visual identity built on one accent color:

- **Title** in deep navy, left-aligned (a document, not a certificate),
  followed by a **thick accent rule** across the page.
- **H1/H2 in the accent blue**, deeper headings in slate — real visual
  hierarchy instead of six shades of black.
- **Body text near-black rather than pure black**, with real line height
  and paragraph spacing.
- **Tables get a colored header band with white bold text, subtle zebra
  striping, hairline grid, and generous cell padding** — plus
  `repeatRows=1`, so a table spanning a page break repeats its header.
- **Blockquotes get a tinted background and an accent bar down the left
  edge** — the convention readers already associate with a quotation.
  This needs a single-cell Table (`LINEBEFORE`), since a ParagraphStyle
  border would draw on all four sides.
- **Code blocks get a tinted background** and padding.

The palette lives in **`theme.json`** at the root of this skill — edit it
to change colors without touching the script. Every color is a `"#RRGGBB"`
string; keys prefixed with `_` are ignored, so you can leave notes in the
file. Delete the file to fall back to the built-in defaults, or pass
`--theme other-theme.json` to use a different one (this also works in
folder mode with `--jobs` — each worker re-reads the named file). Any
problem with a theme file — missing, unparseable, unknown key, malformed
color — warns on stderr and falls back to the built-in value for just that
key: a bad color never costs you the document.

## When this isn't the right call

If the user needs a document built from an existing template, wants
pixel-precise print design, or needs a PDF form filled or an existing PDF
edited rather than generated fresh from content, don't force this script
-- use whatever richer PDF-authoring/editing skill is available instead.

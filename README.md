<p align="center">
  <img src="assets/logo.svg" width="140" alt="markdown-to-pdf logo">
</p>

<h1 align="center">markdown-to-pdf</h1>

<p align="center">
  <em>Turn Markdown into a real, paginated PDF — without writing a
  one-off reportlab script every time.</em>
</p>

<p align="center">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg">
  <img alt="Python" src="https://img.shields.io/badge/python-3.8%2B-blue.svg">
  <img alt="Model calls in generation" src="https://img.shields.io/badge/model%20calls%20in%20generation-0-brightgreen.svg">
  <img alt="Claude Code skill" src="https://img.shields.io/badge/Claude%20Code-skill-5A45FF.svg">
</p>

<p align="center">
  <a href="#why-this-exists">Why</a> ·
  <a href="#what-you-get">What you get</a> ·
  <a href="#features">Features</a> ·
  <a href="#installing-this-skill-in-claude-code">Install</a> ·
  <a href="#usage">Usage</a> ·
  <a href="#limitations">Limitations</a>
</p>

---

It's a Claude Code **skill**: a `SKILL.md` file plus the script itself
(`scripts/markdown_to_pdf.py`) — a **deterministic, standalone Python
tool**, no model/LLM involved in generation. Write the content as
Markdown, get a real, automatically paginated `.pdf` back.

This is the fourth and last of this family's write-direction skills,
alongside
[`markdown-to-docx`](https://github.com/DalCorsoMarco/markdown-to-docx),
[`markdown-to-pptx`](https://github.com/DalCorsoMarco/markdown-to-pptx), and
[`markdown-to-xlsx`](https://github.com/DalCorsoMarco/markdown-to-xlsx) —
and the counterpart to
[`pdf-to-markdown`](https://github.com/DalCorsoMarco/pdf-to-markdown),
the skill that started this whole family.

## Why this exists

Asked to produce a `.pdf`, a model without a tool for it improvises a
one-off script calling a PDF library directly, working out styles and
layout from scratch every time. This skill inverts that: the model writes
plain Markdown, and this script builds the actual document using
[reportlab](https://www.reportlab.com/)'s layout engine — which handles
pagination and text wrapping automatically, rather than hand-positioning
text at fixed coordinates.

Unlike this family's other three write-direction skills, a PDF has no
editable structured model underneath once it's built, so there's no
lossless round trip to verify against `pdf-to-markdown` — see
[SKILL.md](SKILL.md#the-odd-one-out-in-this-family) for what that means
and how correctness was verified instead (rendering the output to an
image and inspecting it directly, not reading it back heuristically).

## What you get

- **Free and instant.** 0 tokens, always.
- **Real pagination, not manual layout.** Long content automatically
  flows across pages; a large image that doesn't fit the current page
  moves to the next one on its own.
- **Real styled headings, lists, and tables** — not text that merely
  looks like them.
- **Real clickable hyperlinks.**
- **Remembers what it's done.** CRC32 stamped into the PDF's own Subject
  metadata (read back via PyMuPDF, since reportlab itself can't read
  PDFs); re-running on unchanged Markdown is an instant no-op.
- **Scales to a folder.** `--jobs N` generates several PDFs at once.

## Features

- **Title + Heading1–6 styles**, matching Markdown's own heading range.
- **Bold, italic, strikethrough, code, and real hyperlinks.**
- **Nested bullet/numbered lists** with correct per-level indentation.
- **Real tables** with wrapped cell text.
- **Embedded images**, scaled to the page width and auto-paginated.
- **Blockquotes and horizontal rules.**
- **A styled default look, not black-on-white** — accent-colored title
  rule and headings, tables with a colored header band + zebra striping +
  repeating header across page breaks, and blockquotes with a tinted
  background and left accent bar. See
  [SKILL.md](SKILL.md#default-styling).
- **Colors live in `theme.json`**, not in the code — edit that file to
  restyle the output, or pass `--theme other.json`. Malformed values warn
  and fall back rather than failing the run.
- **Built-in memory** via the PDF's own Subject metadata field;
  `--list-cache` (or `/markdown-to-pdf:list-cache`) reads it back.
- **Single file or whole folders**, optionally recursive, `--jobs N` for
  multi-file speedup.

## Installing this skill in Claude Code

```
/plugin marketplace add DalCorsoMarco/markdown-to-pdf
/plugin install markdown-to-pdf@markdown-to-pdf-repo
```

Or clone manually into `~/.claude/skills/markdown-to-pdf/` or
`<project>/.claude/skills/markdown-to-pdf/`.

> [!NOTE]
> Cowork / cloud sessions don't read `~/.claude/skills/` from your
> machine — only project skills committed to a repo, or skills packaged
> for the Cowork plugin format, carry over there.

## Requirements

| Purpose | Package | Install |
|---|---|---|
| Markdown parsing | [mistune](https://pypi.org/project/mistune/) | `pip install mistune` |
| PDF layout/generation | [reportlab](https://pypi.org/project/reportlab/) | `pip install reportlab` |
| Reading back this script's own cache marker | [PyMuPDF](https://pypi.org/project/PyMuPDF/) (`fitz`) | `pip install pymupdf` |

```bash
pip install mistune reportlab pymupdf
```

## Usage

**Single file:**
```bash
python3 scripts/markdown_to_pdf.py input.md [output.pdf] [--force]
```

**A whole folder:**
```bash
python3 scripts/markdown_to_pdf.py folder/ [output_folder] [--recursive] [--force] [--jobs N]
```

**What's already been generated:**
```bash
python3 scripts/markdown_to_pdf.py --list-cache folder/ [--recursive]
```

### Flags

| Flag | Effect |
|---|---|
| `--force` | Regenerate even if the Markdown's CRC32 already matches the existing `.pdf` |
| `--jobs N` | Folder mode only: generate up to N files at once (default: CPU count; `--jobs 1` forces one-at-a-time) |
| `--theme PATH` | Use a different theme file instead of this skill's own `theme.json` |
| `--list-cache` | List already-generated files instead of converting |

## Limitations

- **No lossless round trip through `pdf-to-markdown`** — a PDF has no
  editable structure to read back exactly, unlike this family's other
  three write-direction skills. Correctness is verified by rendering the
  output and inspecting it, not by re-reading it heuristically.
- **Images can't sit inline within paragraph text** — an image is placed
  as its own block right after the paragraph/heading it appeared under.
- **No nested tables**, and table column widths are automatic, not
  hinted from the Markdown.
- **Formatting nested inside a hyperlink's own text isn't reconstructed.**
- **Verified against a synthetic Markdown document** covering headings,
  nested bulleted/numbered lists, a table, a hyperlink, inline formatting
  (including a literal `&` to confirm XML-escaping correctness), a
  blockquote, a horizontal rule, and an image that auto-paginated to a
  second page — checked by rendering the actual PDF to an image, not by
  reading it back — not yet against a large body of real-world Markdown.

See `SKILL.md` for the full design rationale.

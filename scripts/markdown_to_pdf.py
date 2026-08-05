#!/usr/bin/env python3
"""
Deterministic Markdown -> PDF generator.

Write-direction counterpart to pdf-to-markdown, and the last of this
family's four markdown-to-X skills. Same reasoning as its siblings: asked
for a PDF, a model without a tool for it improvises a one-off script every
time -- here using reportlab directly, working out styles, flowables, and
layout from scratch. This script does that once; the model's job is just
writing Markdown.

PDF is a different kind of target than DOCX/PPTX/XLSX, though: those are
editable structured formats this family can also read back losslessly.
A PDF is a *rendered, paginated* format -- there's no "real Word list" or
"real Excel formula" equivalent to build, only a laid-out page. This
script uses reportlab's Platypus layout engine (SimpleDocTemplate +
Paragraph/ListFlowable/Table/Image flowables), which handles pagination,
page breaks, and text wrapping automatically -- the alternative (manual
per-line text positioning) would reinvent a worse version of exactly that.

What each Markdown construct becomes:
  - # .. ###### -> the first H1 becomes the "Title" paragraph style; every
    other heading maps to "Heading1".."Heading6" (reportlab's own default
    stylesheet already defines exactly six of these).
  - **bold**, *italic*, `code`, ~~strikethrough~~, [text](url) all become
    reportlab's own inline markup (a real, clickable link).
  - Bullet/ordered lists become real nested ListFlowables.
  - Tables become real Table flowables, with each cell's text wrapped in
    its own Paragraph so long content wraps instead of overflowing.
  - ![alt](path) embeds the real image (resolved relative to the Markdown
    file's own directory), scaled to fit the page width while preserving
    its aspect ratio.
  - > quoted text becomes an indented, italicized paragraph.
  - A thematic break (---) becomes a real horizontal rule line, not a
    page break.
  - A fenced code block becomes a monospaced, whitespace-preserving block.

Usage:
    python3 markdown_to_pdf.py <input.md> [output.pdf] [--force]
    python3 markdown_to_pdf.py <folder> [output_folder] [--recursive] [--force] [--jobs N]
    python3 markdown_to_pdf.py --list-cache <folder> [--recursive]

Memory: unlike this family's other markdown-to-X skills, there's no
document-editing library available to *read back* a PDF's own metadata
(reportlab only writes PDFs) -- pdf-to-markdown's own dependency, PyMuPDF
(fitz), is reused here purely for that read-back, stamping the source
Markdown's path and CRC32 into the PDF's own Subject metadata field.
Re-running on unchanged Markdown is an instant no-op; --force regenerates
anyway.
"""
import sys
import os
import re
import io
import json
import zlib
import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from xml.sax.saxutils import escape as xml_escape

import mistune
import fitz  # PyMuPDF -- only used here to read back this script's own
             # metadata marker for the CRC32 cache check; pdf-to-markdown's
             # own established dependency for reading PDFs, reused rather
             # than adding a second PDF-reading library
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, ListFlowable, ListItem, Table, TableStyle,
    HRFlowable, Preformatted, Image, Spacer,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib import colors
from PIL import Image as PILImage

FRONT_MATTER_FIELD_RE = re.compile(r"^([a-z0-9_]+):\s*(.*?)\s*$", re.MULTILINE)
PAGE_WIDTH, PAGE_HEIGHT = LETTER
MARGIN = 0.75 * inch
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
MAX_IMAGE_HEIGHT = 6 * inch

# ---------------------------------------------------------------------------
# Theme -- the built-in defaults below are overridden by theme.json in the
# skill's own root directory (a sibling of scripts/), so the palette can be
# changed without touching this file. A missing theme.json is normal and
# silent; a malformed one warns and falls back rather than failing the
# conversion, since a bad color is never worth losing the document over.
# ---------------------------------------------------------------------------

DEFAULT_THEME = {
    "accent": "#1F6FEB",            # headings, rules, table header band
    "title": "#142B4A",             # deep navy
    "heading_deep": "#2E3A4A",      # lower-level headings, less shouty than the accent
    "body": "#24292F",              # near-black, but not pure black
    "quote_text": "#3A4A5E",
    "quote_bg": "#F4F7FB",
    "table_header_bg": "#1F6FEB",
    "table_header_text": "#FFFFFF",
    "table_band_bg": "#F4F7FB",     # subtle zebra striping
    "table_grid": "#D0D7DE",
    "code_bg": "#F4F6F8",
}

THEME_FILENAME = "theme.json"
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _default_theme_path():
    """<skill root>/theme.json -- this script lives in <skill root>/scripts/."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), THEME_FILENAME
    )


def read_theme(explicit_path=None):
    """Returns DEFAULT_THEME merged with whatever theme.json provides.
    Every failure mode (file missing, unreadable, not an object, unknown
    key, malformed color) falls back to the built-in value for just that
    key and keeps going -- a theme file is a convenience, never a
    precondition for producing the document."""
    theme = dict(DEFAULT_THEME)
    path = explicit_path or _default_theme_path()
    if not os.path.exists(path):
        if explicit_path:  # silent when it's just the optional default file
            print(f"Theme file not found: {path} -- using built-in defaults", file=sys.stderr)
        return theme
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"Could not read {path} ({exc}) -- using built-in defaults", file=sys.stderr)
        return theme
    if not isinstance(raw, dict):
        print(f"{path}: expected a JSON object -- using built-in defaults", file=sys.stderr)
        return theme

    for key, value in raw.items():
        if key.startswith("_"):
            continue  # "_comment"-style keys are allowed and ignored
        if key not in theme:
            print(f"{path}: ignoring unknown key {key!r}", file=sys.stderr)
            continue
        if not isinstance(value, str) or not HEX_COLOR_RE.match(value):
            print(
                f"{path}: {key!r} must be a \"#RRGGBB\" color, got {value!r} -- "
                f"using default {theme[key]}",
                file=sys.stderr,
            )
            continue
        theme[key] = value
    return theme


def apply_theme(explicit_path=None):
    """Resolves the theme into the module-level color constants the
    rendering code already reads. Called at import (so defaults always
    exist) and again from main()/each worker process when --theme names a
    different file."""
    global ACCENT_COLOR, TITLE_COLOR, HEADING_DEEP_COLOR, BODY_COLOR
    global QUOTE_COLOR, QUOTE_BG_COLOR, TABLE_HEADER_BG, TABLE_HEADER_FG
    global TABLE_BAND_BG, TABLE_GRID_COLOR, CODE_BG_COLOR

    theme = read_theme(explicit_path)
    ACCENT_COLOR = colors.HexColor(theme["accent"])
    TITLE_COLOR = colors.HexColor(theme["title"])
    HEADING_DEEP_COLOR = colors.HexColor(theme["heading_deep"])
    BODY_COLOR = colors.HexColor(theme["body"])
    QUOTE_COLOR = colors.HexColor(theme["quote_text"])
    QUOTE_BG_COLOR = colors.HexColor(theme["quote_bg"])
    TABLE_HEADER_BG = colors.HexColor(theme["table_header_bg"])
    TABLE_HEADER_FG = colors.HexColor(theme["table_header_text"])
    TABLE_BAND_BG = colors.HexColor(theme["table_band_bg"])
    TABLE_GRID_COLOR = colors.HexColor(theme["table_grid"])
    CODE_BG_COLOR = colors.HexColor(theme["code_bg"])


apply_theme()


# ---------------------------------------------------------------------------
# CRC32 / memory -- stamped into the PDF's own Subject field, read back via
# fitz since reportlab itself has no PDF-reading API.
# ---------------------------------------------------------------------------

def compute_crc32(path):
    crc = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 16)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return format(crc & 0xFFFFFFFF, "08x")


def _read_pdf_subject_marker(pdf_path):
    if not os.path.exists(pdf_path):
        return None
    try:
        doc = fitz.open(pdf_path)
        subject = doc.metadata.get("subject") or ""
    except Exception:  # noqa: BLE001 - not a PDF we can read, treat as "no memory"
        return None
    if not subject.startswith("markdown-to-pdf:"):
        return None
    return dict(FRONT_MATTER_FIELD_RE.findall(subject))


def read_existing_crc32(pdf_path):
    fm = _read_pdf_subject_marker(pdf_path)
    return fm.get("source_crc32") if fm else None


def _stamp_source(doc, md_path, crc32):
    doc.author = "markdown-to-pdf"
    doc.subject = (
        f"markdown-to-pdf:\n"
        f"source_markdown: {os.path.abspath(md_path)}\n"
        f"source_crc32: {crc32}\n"
        f"converted_at: {datetime.datetime.now().isoformat(timespec='seconds')}"
    )


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def build_styles():
    """reportlab's stock stylesheet is entirely black Times/Helvetica with
    minimal spacing -- correct, but it reads as a raw dump. These overrides
    give the output a consistent, restrained visual identity: one accent
    color used for the title and headings, a softer-than-black body text,
    real paragraph spacing, and a blockquote that actually looks quoted
    (accent left rule + tinted background) rather than merely indented."""
    styles = getSampleStyleSheet()

    styles["Title"].textColor = TITLE_COLOR
    styles["Title"].fontName = "Helvetica-Bold"
    styles["Title"].fontSize = 26
    styles["Title"].leading = 32
    styles["Title"].spaceAfter = 6
    styles["Title"].alignment = 0  # left, not centered -- reads as a document, not a certificate

    for level in range(1, 7):
        style = styles[f"Heading{level}"]
        style.textColor = ACCENT_COLOR if level <= 2 else HEADING_DEEP_COLOR
        style.fontName = "Helvetica-Bold"
        style.spaceBefore = 14 if level <= 2 else 10
        style.spaceAfter = 5

    styles["Normal"].textColor = BODY_COLOR
    styles["Normal"].fontName = "Helvetica"
    styles["Normal"].fontSize = 10.5
    styles["Normal"].leading = 15
    styles["Normal"].spaceAfter = 6

    styles["Code"].textColor = BODY_COLOR
    styles["Code"].backColor = CODE_BG_COLOR
    styles["Code"].borderPadding = 6
    styles["Code"].leftIndent = 6

    # Background and the left accent rule are applied by the Table wrapper
    # in render_blockquote, not here -- a ParagraphStyle border draws on all
    # four sides, and only a table cell can give a left-only accent bar.
    styles.add(ParagraphStyle(
        name="BlockQuote", parent=styles["Normal"],
        textColor=QUOTE_COLOR, spaceAfter=0,
    ))
    return styles


def title_rule_flowable():
    """The accent rule drawn immediately under the document title -- the
    single cheapest thing that makes the first page look designed rather
    than default."""
    return HRFlowable(
        width="100%", thickness=2.5, color=ACCENT_COLOR,
        spaceBefore=2, spaceAfter=12,
    )


# ---------------------------------------------------------------------------
# Inline markup -- builds a reportlab Paragraph markup string, escaping raw
# text (reportlab's Paragraph markup is itself a small XML dialect, so
# literal &/</> in content must be escaped before any <b>/<i>/<a> tags are
# added around it).
# ---------------------------------------------------------------------------

def extract_plain_text(children):
    parts = []
    for child in children:
        if child["type"] == "text":
            parts.append(child["raw"])
        elif child["type"] == "codespan":
            parts.append(child["raw"])
        elif "children" in child:
            parts.append(extract_plain_text(child["children"]))
    return "".join(parts)


def render_inline_markup(children, pending_images):
    """Returns a reportlab markup string. Any image found inline is
    appended to pending_images (list of url) rather than embedded in the
    text -- reportlab's Paragraph markup has no inline-image support, so
    an image is rendered as its own flowable placed right after the
    paragraph it appeared in (see render_blocks)."""
    parts = []
    for child in children:
        t = child["type"]
        if t == "text":
            parts.append(xml_escape(child["raw"]))
        elif t == "codespan":
            parts.append(f'<font face="Courier">{xml_escape(child["raw"])}</font>')
        elif t == "strong":
            parts.append(f"<b>{render_inline_markup(child['children'], pending_images)}</b>")
        elif t == "emphasis":
            parts.append(f"<i>{render_inline_markup(child['children'], pending_images)}</i>")
        elif t == "strikethrough":
            parts.append(f"<strike>{render_inline_markup(child['children'], pending_images)}</strike>")
        elif t in ("linebreak", "softbreak"):
            parts.append("<br/>")
        elif t == "link":
            url = xml_escape(child["attrs"]["url"])
            text = render_inline_markup(child["children"], pending_images)
            parts.append(f'<a href="{url}" color="blue"><u>{text}</u></a>')
        elif t == "image":
            pending_images.append(child["attrs"]["url"])
        else:
            if "raw" in child:
                parts.append(xml_escape(child["raw"]))
            elif "children" in child:
                parts.append(render_inline_markup(child["children"], pending_images))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def build_image_flowable(url, md_dir):
    path = url if os.path.isabs(url) else os.path.join(md_dir, url)
    if not os.path.exists(path):
        print(f"Skipped an image that doesn't resolve locally: {url}", file=sys.stderr)
        return None
    try:
        with PILImage.open(path) as im:
            px_w, px_h = im.size
        aspect = px_h / px_w if px_w else 1
        width = CONTENT_WIDTH
        height = width * aspect
        if height > MAX_IMAGE_HEIGHT:
            height = MAX_IMAGE_HEIGHT
            width = height / aspect
        return Image(path, width=width, height=height)
    except Exception as exc:  # noqa: BLE001 - one bad image shouldn't fail the document
        print(f"Skipped an image reportlab couldn't embed ({url}): {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Block-level rendering
# ---------------------------------------------------------------------------

def render_list(token, styles, md_dir, level=0):
    ordered = token["attrs"]["ordered"]
    bullet_type = "1" if ordered else "bullet"
    items = []
    for item in token["children"]:
        item_flowables = []
        for child in item["children"]:
            if child["type"] in ("block_text", "paragraph"):
                pending_images = []
                markup = render_inline_markup(child["children"], pending_images)
                item_flowables.append(Paragraph(markup, styles["Normal"]))
                for url in pending_images:
                    img = build_image_flowable(url, md_dir)
                    if img:
                        item_flowables.append(img)
            elif child["type"] == "list":
                item_flowables.append(render_list(child, styles, md_dir, level=level + 1))
            elif child["type"] == "block_code":
                item_flowables.append(Preformatted(child["raw"].rstrip("\n"), styles["Code"]))
        if item_flowables:
            items.append(ListItem(item_flowables))
    return ListFlowable(items, bulletType=bullet_type, leftIndent=18 * (level + 1))


def render_table(token, styles):
    head = next((c for c in token["children"] if c["type"] == "table_head"), None)
    body = next((c for c in token["children"] if c["type"] == "table_body"), None)
    if head is None:
        return None
    rows_tokens = [head["children"]] + [r["children"] for r in (body["children"] if body else [])]
    header_style = ParagraphStyle(
        "TableHeaderCell", parent=styles["Normal"],
        textColor=TABLE_HEADER_FG, fontName="Helvetica-Bold", spaceAfter=0,
    )
    body_style = ParagraphStyle("TableBodyCell", parent=styles["Normal"], spaceAfter=0)

    data = []
    for r, row_cells in enumerate(rows_tokens):
        row = []
        for cell_token in row_cells:
            pending_images = []  # images inside table cells aren't placeable in a grid cell -- text only
            markup = render_inline_markup(cell_token["children"], pending_images)
            row.append(Paragraph(markup, header_style if r == 0 else body_style))
        data.append(row)

    table = Table(data, hAlign="LEFT", repeatRows=1)  # header repeats if the table spans pages
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_GRID_COLOR),
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TABLE_BAND_BG]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def render_blockquote(token, styles, md_dir, _nested=False):
    """Renders a blockquote as a tinted block with an accent bar down its
    left edge -- the visual convention readers already associate with a
    quotation. The bar needs a single-cell Table (LINEBEFORE), since a
    ParagraphStyle border would draw on all four sides. A nested quote
    returns bare paragraphs instead, so it composes into its parent's
    wrapper rather than nesting one bordered box inside another."""
    inner = []
    for child in token["children"]:
        if child["type"] == "paragraph":
            pending_images = []
            markup = render_inline_markup(child["children"], pending_images)
            inner.append(Paragraph(f"<i>{markup}</i>", styles["BlockQuote"]))
        elif child["type"] == "block_quote":
            inner.extend(render_blockquote(child, styles, md_dir, _nested=True))
    if not inner or _nested:
        return inner

    wrapper = Table([[inner]], colWidths=[CONTENT_WIDTH], hAlign="LEFT")
    wrapper.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT_COLOR),
        ("BACKGROUND", (0, 0), (-1, -1), QUOTE_BG_COLOR),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [wrapper]


def render_blocks(tokens, styles, md_dir, seen_h1):
    story = []
    for token in tokens:
        t = token["type"]
        if t == "heading":
            level = token["attrs"]["level"]
            pending_images = []
            markup = render_inline_markup(token["children"], pending_images)
            if level == 1 and not seen_h1[0]:
                story.append(Paragraph(markup, styles["Title"]))
                story.append(title_rule_flowable())
                seen_h1[0] = True
            else:
                style_name = f"Heading{min(level, 6)}"
                story.append(Paragraph(markup, styles[style_name]))
            for url in pending_images:
                img = build_image_flowable(url, md_dir)
                if img:
                    story.append(img)
        elif t == "paragraph":
            pending_images = []
            markup = render_inline_markup(token["children"], pending_images)
            if markup.strip():
                story.append(Paragraph(markup, styles["Normal"]))
            for url in pending_images:
                img = build_image_flowable(url, md_dir)
                if img:
                    story.append(img)
        elif t == "list":
            story.append(render_list(token, styles, md_dir))
        elif t == "table":
            table = render_table(token, styles)
            if table:
                story.append(table)
        elif t == "block_quote":
            story.extend(render_blockquote(token, styles, md_dir))
        elif t == "block_code":
            story.append(Preformatted(token["raw"].rstrip("\n"), styles["Code"]))
        elif t == "thematic_break":
            story.append(Spacer(1, 6))
            story.append(HRFlowable(width="100%", color=TABLE_GRID_COLOR))
            story.append(Spacer(1, 6))
        elif t == "blank_line":
            continue
        else:
            if "raw" in token:
                story.append(Paragraph(xml_escape(token["raw"]), styles["Normal"]))
        story.append(Spacer(1, 4))
    return story


# ---------------------------------------------------------------------------
# Top-level conversion
# ---------------------------------------------------------------------------

def build_pdf(md_path, out_path):
    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()

    md = mistune.create_markdown(renderer=None, plugins=["table", "strikethrough"])
    tokens = md(text)

    styles = build_styles()
    md_dir = os.path.dirname(os.path.abspath(md_path))
    seen_h1 = [False]
    story = render_blocks(tokens, styles, md_dir, seen_h1)

    doc = SimpleDocTemplate(
        out_path, pagesize=LETTER,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
    )
    crc32 = compute_crc32(md_path)
    _stamp_source(doc, md_path, crc32)
    doc.build(story)
    return crc32


def convert(md_path, out_path=None, force=False):
    if out_path is None:
        out_path = os.path.splitext(md_path)[0] + ".pdf"

    crc32 = compute_crc32(md_path)
    if not force and read_existing_crc32(out_path) == crc32:
        return out_path, False

    print(f"Reading {md_path}...")
    build_pdf(md_path, out_path)
    print(f"Wrote: {out_path}")
    return out_path, True


def _convert_worker(args):
    md_path, out_path, force, theme_path = args
    # A worker process re-imports this module, which re-applies the DEFAULT
    # theme -- so a --theme override has to be re-applied here too, or
    # folder mode with --jobs would silently ignore it.
    if theme_path:
        apply_theme(theme_path)
    return convert(md_path, out_path, force=force)


def find_markdown(folder, recursive=False):
    if recursive:
        matches = []
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(".md"):
                    matches.append(os.path.join(root, name))
        return sorted(matches)
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(".md")
    )


def find_pdf(folder, recursive=False):
    if recursive:
        matches = []
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(".pdf"):
                    matches.append(os.path.join(root, name))
        return sorted(matches)
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(".pdf")
    )


def list_cache(folder, recursive=False):
    entries = []
    for pdf_path in find_pdf(folder, recursive=recursive):
        fm = _read_pdf_subject_marker(pdf_path)
        if not fm or "source_crc32" not in fm:
            continue
        source_md = fm.get("source_markdown", "?")
        if not os.path.exists(source_md):
            status = "source Markdown missing"
        elif compute_crc32(source_md) == fm["source_crc32"]:
            status = "up to date"
        else:
            status = "stale (Markdown changed)"
        entries.append({"pdf_path": pdf_path, "status": status, "converted_at": fm.get("converted_at", "?")})

    if not entries:
        print(f"No generated (.pdf) files found in {folder}", file=sys.stderr)
        return entries

    print(f"{'Status':<22}  {'Last generated':<19}  PDF file")
    for e in entries:
        print(f"{e['status']:<22}  {e['converted_at']:<19}  {e['pdf_path']}")
    print(f"---\n{len(entries)} file(s)")
    return entries


def convert_folder(folder, out_dir=None, recursive=False, force=False, jobs=1, theme_path=None):
    files = find_markdown(folder, recursive=recursive)
    if not files:
        print(f"No Markdown files found in {folder}", file=sys.stderr)
        return

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    tasks = []
    for md_path in files:
        if out_dir:
            rel = os.path.relpath(md_path, folder)
            out_path = os.path.join(out_dir, os.path.splitext(rel)[0] + ".pdf")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
        else:
            out_path = None
        tasks.append((md_path, out_path))

    if jobs > 1 and len(tasks) > 1:
        jobs = min(jobs, len(tasks))
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futures = {ex.submit(_convert_worker, (p, o, force, theme_path)): p for p, o in tasks}
            for fut in as_completed(futures):
                md_path = futures[fut]
                try:
                    result, written = fut.result()
                    if not written:
                        print(f"Up to date, skipped: {result}")
                except Exception as exc:  # noqa: BLE001
                    print(f"FAILED on {md_path}: {exc}", file=sys.stderr)
    else:
        for md_path, out_path in tasks:
            try:
                result, written = convert(md_path, out_path, force=force)
                if not written:
                    print(f"Up to date, skipped: {result}")
            except Exception as exc:  # noqa: BLE001
                print(f"FAILED on {md_path}: {exc}", file=sys.stderr)


def main():
    raw = sys.argv[1:]
    recursive = "--recursive" in raw
    force = "--force" in raw
    list_cache_mode = "--list-cache" in raw

    jobs = 1
    if "--jobs" in raw:
        idx = raw.index("--jobs")
        try:
            jobs = max(1, int(raw[idx + 1]))
        except (IndexError, ValueError):
            print("--jobs requires an integer, e.g. --jobs 4", file=sys.stderr)
            sys.exit(1)

    theme_path = None
    if "--theme" in raw:
        idx = raw.index("--theme")
        try:
            theme_path = raw[idx + 1]
        except IndexError:
            print("--theme requires a path, e.g. --theme my-theme.json", file=sys.stderr)
            sys.exit(1)
        apply_theme(theme_path)

    positional = []
    skip_next = False
    for a in raw:
        if skip_next:
            skip_next = False
            continue
        if a in ("--recursive", "--force", "--list-cache"):
            continue
        if a in ("--jobs", "--theme"):
            skip_next = True
            continue
        positional.append(a)

    usage = (
        "Usage:\n"
        "  Single file:  markdown_to_pdf.py <input.md> [output.pdf] [--force] [--theme theme.json]\n"
        "  Whole folder: markdown_to_pdf.py <folder> [output_folder] [--recursive] [--force] [--jobs N] [--theme theme.json]\n"
        "  List cache:   markdown_to_pdf.py --list-cache <folder> [--recursive]\n"
        "                (--theme overrides the colors in this skill's own\n"
        "                 theme.json; see that file for the available keys)"
    )

    if list_cache_mode:
        if len(positional) != 1:
            print(usage, file=sys.stderr)
            sys.exit(1)
        list_cache(positional[0], recursive=recursive)
        return

    if len(positional) not in (1, 2):
        print(usage, file=sys.stderr)
        sys.exit(1)

    input_path = positional[0]
    second = positional[1] if len(positional) == 2 else None

    if os.path.isdir(input_path):
        convert_folder(input_path, out_dir=second, recursive=recursive, force=force, jobs=jobs, theme_path=theme_path)
    else:
        result, written = convert(input_path, second, force=force)
        if not written:
            print(f"Up to date, skipped: {result}")


if __name__ == "__main__":
    main()

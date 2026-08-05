---
description: List generated PDFs in a folder, with last-generated date and whether each one is still up to date with its source Markdown file. Use when the user asks what's already been generated, or wants a status/cache report before running a big batch.
argument-hint: [folder-path] [--recursive]
arguments: folder
allowed-tools: Bash(python3 scripts/markdown_to_pdf.py --list-cache *)
---

Run this skill's cache-listing mode, without converting anything:

```
python3 scripts/markdown_to_pdf.py --list-cache "$folder"
```

Add `--recursive` if the user's request implies subfolders too.

Report the result as a short table or list: generated file, last
generated date, and status (`up to date`, `stale (Markdown changed)`, or
`source Markdown missing`). If nothing is cached yet, say so plainly
instead of inventing rows.

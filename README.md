<p align="center">
  <img src="assets/docmarshal-wordmark.png" alt="DocMarshal" width="680">
</p>

# DocMarshal

DocMarshal is a Windows desktop app for sorting fleet and tool records. It reads searchable PDFs, suggests where they belong, and puts the final decision in front of a person before anything is filed.

I built it for the day-to-day reality of DOT paperwork: scans arrive with inconsistent names, identifiers are not always obvious, and a confident wrong answer is worse than leaving a document for review.

[![License: GPL v3+](https://img.shields.io/badge/License-GPLv3%2B-blue.svg)](LICENSE)

> [!IMPORTANT]
> DocMarshal is a working prototype. It has extensive automated tests and conservative file checks, but it is not a substitute for backups or normal records-management controls. Set it up against test folders first, confirm the proposed filing paths, and keep production data backed up.

## What it does

- Reviews searchable PDF scans in a dark Windows interface
- Suggests the unit or tool, document type, controlling date, filename, and destination
- Leaves weak or conflicting matches in Needs Review
- Files a verified copy only after **Approve and File Copy** or Enter
- Runs OCR in bulk while preserving originals and isolating per-file failures
- Scans from an Epson ES-400II through NAPS2/TWAIN at 600 DPI, full color, and duplex
- Supports one combined scan, one PDF per page, or splitting at blank separator pages
- Marks duplicates as a separate audited disposition
- Uses **Remove Document** for documents that should leave the active queue; this is an archived disposition, not an irreversible delete button
- Manages fleet assets, tools, calibration dates, and certification history
- Browses fleet and tool records in Virtual Binder and opens the normal Windows print dialog for the current binder PDF
- Keeps a review session and append-only audit history

## How the review flow works

1. Put searchable PDFs in the configured Incoming folder, import copies, or use **Scan Documents**.
2. Click **Refresh Incoming**. DocMarshal analyzes new or changed files and reuses verified results for unchanged files.
3. Check the PDF and the proposed details in Sort.
4. Approve it, mark it as a duplicate, or remove it from the active queue.
5. Approved copies go to the configured fleet or tool folder. Files that still need attention stay in Incoming.

DocMarshal compares SHA-256 fingerprints before sensitive filing and archive operations. It also avoids overwriting existing destination files. These checks reduce the chance of filing or removing the wrong file, but they do not replace backups.

## Requirements

- Windows 10 or Windows 11
- Python 3.11 or newer
- Searchable PDF input
- NAPS2 if you want scanner integration or searchable-PDF OCR
- Adobe Acrobat if you want Virtual Binder to open its visible print dialog

Python packages are pinned in `requirements.txt`.

## Set it up

1. Clone or download this repository.
2. Copy `config.example.json` to `config.json`.
3. Change the paths in `config.json` to match your test folders.
4. Double-click `Setup DocMarshal.bat`.
5. Start DocMarshal from its desktop shortcut or `Launch DocMarshal.bat`.
6. Process a few copies of real-world documents before pointing it at production folders.

`config.json`, local databases, review sessions, and operational records are intentionally excluded from Git.

## Main configuration paths

| Setting | Used for |
|---|---|
| `scan_incoming` | PDFs waiting for review |
| `scan_processed` | Processed and duplicate archives |
| `scan_approved` | Approved-document archive |
| `scan_exceptions` | Removed-document and exception archives |
| `scan_review` | Review session, audit log, and related working records |
| `fleet_workbook` | Fleet asset workbook used for matching |
| `fleet_database` | Local SQLite fleet database |
| `manual_assets_registry` | Assets added through DocMarshal |
| `unit_folders_root` | Main fleet document folders |
| `farm_asset_folders_root` | Secondary or farm asset folders |

The application Settings screen includes additional paths for tool records, scanner integration, OCR, and printing.

## File names

DocMarshal uses short, predictable names:

| Record | Pattern |
|---|---|
| DOT inspection | `{UNIT}_DOT_{MM-DD-YYYY}.pdf` |
| Repair or maintenance | `{UNIT}_RP_{MM-DD-YYYY}.pdf` |
| Registration | `{UNIT}_REG_{MM-DD-YYYY}.pdf` |
| Title | `{UNIT}_TITLE_{MM-DD-YYYY}.pdf` |
| Certificate of origin | `{UNIT}_CERTORIGIN_{MM-DD-YYYY}.pdf` |
| CAB Card | `{UNIT}_CAB_{MM-DD-YYYY}.pdf` |
| Insurance | `{UNIT}_INS_{MM-DD-YYYY}.pdf` |
| Other supported record | `{UNIT}_MISC_{MM-DD-YYYY}.pdf` |

Continuation pages add `_PG2`, `_PG3`, and so on before `.pdf`.

## Development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m compileall -q dotdocs tests
```

GitHub Actions runs compilation and the full test suite on Windows.

## Project artwork

Application artwork lives in `assets/`:

- `docmarshal-icon.png` is the transparent square icon.
- `docmarshal.ico` contains the Windows icon sizes.
- `docmarshal-wordmark.png` is the wordmark shown above.

The source conversion is documented in `scripts/build_brand_assets.py`.

## License

Copyright © 2026 John Combs.

DocMarshal is free software under the GNU General Public License, version 3 or, at your option, any later version. See [LICENSE](LICENSE).

The software comes without a warranty. The license has the full terms.
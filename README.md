<p align="center">
  <img src="assets/docmarshal-wordmark.png" alt="DocMarshal" width="680">
</p>

# DocMarshal

**DocMarshal** is a safety-first Windows desktop application for reviewing, naming, and filing fleet compliance documents. It analyzes searchable PDFs, proposes document metadata, and requires an explicit human approval before filing a copy.

[![License: GPL v3+](https://img.shields.io/badge/License-GPLv3%2B-blue.svg)](LICENSE)

## Features

- Modern dark interface with navy, blue, and marshal-gold branding
- Searchable-PDF analysis with conservative, fail-closed field extraction
- Human-readable review notes and document names
- Explicit **Approve and File Copy** workflow
- Unit, document-type, controlling-date, and continuation-page correction
- Collision protection—existing destination files are never overwritten
- SHA-256 source verification for filing and archive operations
- Duplicate and Not DOT archives with audited Restore to Active
- Persistent manual asset registration
- Review-session and append-only audit records

## Safety model

DocMarshal is supervised automation. It does not silently route uncertain documents. Missing or ambiguous fields remain in **Needs Review**, and the operator must approve the displayed metadata before a production copy is filed. Incoming source PDFs are preserved during normal approval.

Always test your configuration against non-production folders before using it with live fleet records.

## Requirements

- Windows 10 or Windows 11
- Python 3.11+
- Searchable PDF input; NAPS2 is one compatible scanning option

Python dependencies are pinned in `requirements.txt`.

## Setup

1. Clone or download this repository.
2. Copy `config.example.json` to `config.json`.
3. Edit `config.json` for your incoming, review, archive, fleet database, and destination folders.
4. Double-click **`Setup DocMarshal.bat`**.
5. Start the application from the **DocMarshal** desktop shortcut or **`Launch DocMarshal.bat`**.

`config.json` and local operational data are intentionally excluded from Git.

## Configuration

| Key | Purpose |
|---|---|
| `scan_incoming` | Searchable PDFs waiting to be reviewed |
| `scan_processed` | Processed and duplicate archive root |
| `scan_approved` | Approved-document operational path |
| `scan_exceptions` | Exceptions and Not DOT archive root |
| `scan_review` | Active review session and audit records |
| `fleet_workbook` | Source fleet asset workbook |
| `fleet_database` | Generated local fleet database |
| `manual_assets_registry` | Persistent GUI-added asset registry |
| `unit_folders_root` | Primary fleet destination folders |
| `farm_asset_folders_root` | Secondary/farm destination folders |

## Document naming

- DOT inspection: `{UNIT}_DOT_{MM-DD-YYYY}.pdf`
- Repair or maintenance: `{UNIT}_RP_{MM-DD-YYYY}.pdf`
- Registration: `{UNIT}_REG_{MM-DD-YYYY}.pdf`
- Title: `{UNIT}_TITLE_{MM-DD-YYYY}.pdf`
- Certificate of origin: `{UNIT}_CERTORIGIN_{MM-DD-YYYY}.pdf`
- Insurance: `{UNIT}_INS_{MM-DD-YYYY}.pdf`

Continuation pages use `_PG2`, `_PG3`, and later suffixes immediately before `.pdf`.

## Development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest tests -q
.venv/Scripts/python -m compileall -q dotdocs tests
```

The GitHub Actions workflow runs compilation and the complete test suite on Windows.

## Branding assets

Runtime assets are stored under `assets/`:

- `docmarshal-icon.png` — transparent square application icon
- `docmarshal.ico` — multi-resolution Windows icon
- `docmarshal-wordmark.png` — transparent project wordmark

`scripts/build_brand_assets.py` documents the reproducible conversion used for the supplied source artwork.

## License

Copyright © 2026 John Combs.

DocMarshal is free software licensed under the **GNU General Public License, version 3 or, at your option, any later version**. See [LICENSE](LICENSE).

This software is provided without warranty; see the license for details.

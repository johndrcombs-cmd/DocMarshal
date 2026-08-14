from __future__ import annotations

from pathlib import Path
import subprocess

import fitz


ACROBAT_CANDIDATES = (
    Path(r"C:\Program Files\Adobe\Acrobat DC\Acrobat\Acrobat.exe"),
    Path(r"C:\Program Files\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe"),
    Path(r"C:\Program Files (x86)\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe"),
)


class PrintLaunchError(RuntimeError):
    pass


def _find_acrobat() -> Path:
    for candidate in ACROBAT_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise PrintLaunchError(
        "Adobe Acrobat or Acrobat Reader is required to open the Windows print dialog."
    )


def _validate_pdf(path: Path) -> None:
    if path.suffix.casefold() != ".pdf":
        raise PrintLaunchError("The selected Binder file is not a PDF document.")
    if not path.is_file():
        raise PrintLaunchError(f"The selected Binder PDF is unavailable: {path}")
    try:
        with fitz.open(stream=path.read_bytes(), filetype="pdf") as document:
            if document.page_count < 1:
                raise PrintLaunchError("The selected Binder PDF contains no pages.")
            document.load_page(0)
    except PrintLaunchError:
        raise
    except Exception as error:
        raise PrintLaunchError("The selected Binder file is not a readable PDF document.") from error


def launch_pdf_print_dialog(path: str | Path) -> None:
    pdf = Path(path)
    _validate_pdf(pdf)
    acrobat = _find_acrobat()
    try:
        subprocess.Popen(
            [str(acrobat), "/p", str(pdf)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except OSError as error:
        raise PrintLaunchError(f"The print dialog could not be opened: {error}") from error

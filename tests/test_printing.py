from pathlib import Path

import fitz
import pytest

from dotdocs.printing import PrintLaunchError, launch_pdf_print_dialog


def test_launch_pdf_print_dialog_uses_acrobat_nonblocking_print_dialog(monkeypatch, tmp_path):
    pdf = tmp_path / "binder.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(pdf)
    acrobat = tmp_path / "Acrobat.exe"
    acrobat.write_bytes(b"exe")
    calls = []
    monkeypatch.setattr("dotdocs.printing._find_acrobat", lambda: acrobat)
    monkeypatch.setattr("dotdocs.printing.subprocess.Popen", lambda command, **kwargs: calls.append((command, kwargs)))

    launch_pdf_print_dialog(pdf)

    assert calls == [
        ([str(acrobat), "/p", str(pdf)], {"creationflags": 0x08000000}),
    ]


def test_launch_pdf_print_dialog_rejects_non_pdf_before_launch(monkeypatch, tmp_path):
    source = tmp_path / "binder.txt"
    source.write_text("not PDF", encoding="utf-8")
    monkeypatch.setattr("dotdocs.printing.subprocess.Popen", lambda *_args, **_kwargs: pytest.fail("must not launch"))

    with pytest.raises(PrintLaunchError, match="PDF document"):
        launch_pdf_print_dialog(source)
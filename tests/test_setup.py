from pathlib import Path


def test_setup_installs_tesseract_ocr_when_missing():
    setup = (Path(__file__).parents[1] / "Setup DocMarshal.bat").read_text(encoding="utf-8")

    assert "Tesseract-OCR\\tesseract.exe" in setup
    assert "%LOCALAPPDATA%\\Programs\\Tesseract-OCR" in setup
    assert "UB-Mannheim.TesseractOCR" in setup
    assert "winget install" in setup

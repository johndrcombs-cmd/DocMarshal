from contextlib import nullcontext
from pathlib import Path
import subprocess

import fitz
import pytest

from dotdocs.scanner import (
    SCAN_MODE_BLANK_SEPARATORS,
    SCAN_MODE_COMBINED,
    SCAN_MODE_EACH_PAGE,
    ScannerError,
    build_scan_command,
    scan_to_incoming,
)


def _pdf(path: Path, text: str = "scanned") -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def _multi_page_pdf(path: Path, page_texts: list[str]) -> None:
    document = fitz.open()
    for text in page_texts:
        page = document.new_page(width=612, height=792)
        if text:
            page.insert_text((72, 72), text, fontsize=18)
            page.draw_rect((60, 50, 550, 740), color=(0, 0, 0), width=2)
    document.save(path)
    document.close()


def test_build_scan_command_uses_es400ii_duplex_600_dpi_full_color_and_ocr(tmp_path):
    executable = tmp_path / "NAPS2.Console.exe"
    output = tmp_path / "staging.pdf"

    command = build_scan_command(executable, output)

    assert command == [
        str(executable),
        "--noprofile",
        "--driver", "twain",
        "--device", "EPSON ES-400II",
        "--source", "duplex",
        "--pagesize", "letter",
        "--dpi", "600",
        "--bitdepth", "color",
        "--deskew",
        "--enableocr",
        "--ocrlang", "eng",
        "--output", str(output),
    ]
    assert "--force" not in command


def test_scan_to_incoming_validates_then_atomically_publishes_unique_pdf(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    executable = tmp_path / "NAPS2.Console.exe"
    executable.write_bytes(b"exe")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        _pdf(Path(command[-1]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("dotdocs.scanner.subprocess.run", fake_run)

    first = scan_to_incoming(incoming, executable=executable, timestamp="20260813_160000")
    second = scan_to_incoming(incoming, executable=executable, timestamp="20260813_160000")

    assert [path.name for path in first] == ["DocMarshal_Scan_20260813_160000.pdf"]
    assert [path.name for path in second] == ["DocMarshal_Scan_20260813_160000_2.pdf"]
    assert first[0].is_file() and second[0].is_file()
    assert not (incoming / ".docmarshal-scan-staging").exists()
    with fitz.open(first[0]) as document:
        assert document.page_count == 1
    assert all(call[1]["creationflags"] == subprocess.CREATE_NO_WINDOW for call in calls)


def test_scan_publication_retries_if_destination_appears_during_collision_race(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    executable = tmp_path / "NAPS2.Console.exe"
    executable.write_bytes(b"exe")
    occupied = incoming / "race.pdf"
    occupied.write_bytes(b"existing production scan")
    safe = incoming / "race_2.pdf"
    candidates = iter((occupied, safe))

    def fake_run(command, **_kwargs):
        _pdf(Path(command[-1]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("dotdocs.scanner.subprocess.run", fake_run)
    monkeypatch.setattr("dotdocs.scanner._unique_destination", lambda *_args: next(candidates))

    outputs = scan_to_incoming(incoming, executable=executable, timestamp="20260813_160000")

    assert outputs == [safe]
    assert occupied.read_bytes() == b"existing production scan"
    assert safe.is_file()


def test_scan_to_incoming_rejects_invalid_output_without_publishing(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    executable = tmp_path / "NAPS2.Console.exe"
    executable.write_bytes(b"exe")

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"not-a-pdf")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("dotdocs.scanner.subprocess.run", fake_run)

    with pytest.raises(ScannerError, match="valid PDF"):
        scan_to_incoming(incoming, executable=executable, timestamp="20260813_160000")

    assert list(incoming.glob("*.pdf")) == []
    assert not (incoming / ".docmarshal-scan-staging").exists()


def test_each_page_mode_publishes_one_valid_pdf_per_page(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    executable = tmp_path / "NAPS2.Console.exe"
    executable.write_bytes(b"exe")

    def fake_run(command, **_kwargs):
        _multi_page_pdf(Path(command[-1]), ["First document", "Second document", "Third document"])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("dotdocs.scanner.subprocess.run", fake_run)
    outputs = scan_to_incoming(
        incoming,
        executable=executable,
        timestamp="20260813_160000",
        mode=SCAN_MODE_EACH_PAGE,
    )

    assert [path.name for path in outputs] == [
        "DocMarshal_Scan_20260813_160000_001.pdf",
        "DocMarshal_Scan_20260813_160000_002.pdf",
        "DocMarshal_Scan_20260813_160000_003.pdf",
    ]
    for path in outputs:
        with fitz.open(path) as document:
            assert document.page_count == 1


def test_split_scan_rollback_preserves_replacement_file_it_does_not_own(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    executable = tmp_path / "NAPS2.Console.exe"
    executable.write_bytes(b"exe")
    replacement = b"replacement created by another process"
    published = incoming / "first.pdf"
    calls = 0

    def fake_run(command, **_kwargs):
        _multi_page_pdf(Path(command[-1]), ["First document", "Second document"])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def racing_publish(source, _incoming, _stem):
        nonlocal calls
        calls += 1
        if calls == 1:
            source.rename(published)
            published.write_bytes(replacement)
            return published
        raise ScannerError("injected second publication failure")

    monkeypatch.setattr("dotdocs.scanner.subprocess.run", fake_run)
    monkeypatch.setattr("dotdocs.scanner._move_to_unique", racing_publish)
    monkeypatch.setattr(
        "dotdocs.scanner._lock_verified_owned_file",
        lambda *_args, **_kwargs: nullcontext(),
    )

    with pytest.raises(ScannerError, match="injected"):
        scan_to_incoming(
            incoming,
            executable=executable,
            timestamp="20260813_160000",
            mode=SCAN_MODE_EACH_PAGE,
        )

    assert published.read_bytes() == replacement


@pytest.mark.parametrize("mode", [SCAN_MODE_COMBINED, SCAN_MODE_EACH_PAGE])
def test_scan_rejects_output_replaced_immediately_after_publication(monkeypatch, tmp_path, mode):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    executable = tmp_path / "NAPS2.Console.exe"
    executable.write_bytes(b"exe")
    replacement = b"replacement created before publication completed"
    published = incoming / "replaced.pdf"

    def fake_run(command, **_kwargs):
        _multi_page_pdf(Path(command[-1]), ["First document", "Second document"])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def replace_before_return(source, _incoming, _stem):
        source.rename(published)
        published.write_bytes(replacement)
        return published

    monkeypatch.setattr("dotdocs.scanner.subprocess.run", fake_run)
    monkeypatch.setattr("dotdocs.scanner._move_to_unique", replace_before_return)

    with pytest.raises(ScannerError):
        scan_to_incoming(
            incoming,
            executable=executable,
            timestamp="20260813_160000",
            mode=mode,
        )

    assert published.read_bytes() == replacement


def test_blank_separator_mode_removes_blank_pages_and_publishes_document_groups(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    executable = tmp_path / "NAPS2.Console.exe"
    executable.write_bytes(b"exe")

    def fake_run(command, **_kwargs):
        _multi_page_pdf(Path(command[-1]), ["Document A page 1", "Document A page 2", "", "Document B"])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("dotdocs.scanner.subprocess.run", fake_run)
    outputs = scan_to_incoming(
        incoming,
        executable=executable,
        timestamp="20260813_160000",
        mode=SCAN_MODE_BLANK_SEPARATORS,
    )

    assert [path.name for path in outputs] == [
        "DocMarshal_Scan_20260813_160000_001.pdf",
        "DocMarshal_Scan_20260813_160000_002.pdf",
    ]
    page_counts = []
    for path in outputs:
        with fitz.open(path) as document:
            page_counts.append(document.page_count)
    assert page_counts == [2, 1]


def test_scan_rejects_unknown_scan_mode_before_starting_scanner(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    executable = tmp_path / "NAPS2.Console.exe"
    executable.write_bytes(b"exe")
    calls = []
    monkeypatch.setattr("dotdocs.scanner.subprocess.run", lambda *_args, **_kwargs: calls.append(True))

    with pytest.raises(ScannerError, match="scan mode"):
        scan_to_incoming(incoming, executable=executable, mode="Guess automatically")

    assert calls == []

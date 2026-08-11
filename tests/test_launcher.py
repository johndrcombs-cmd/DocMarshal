from pathlib import Path


def test_launcher_fails_closed_when_windows_mutex_cannot_be_created():
    source = (Path(__file__).parents[1] / "launch_gui.pyw").read_text(encoding="utf-8")
    assert "ctypes.WinDLL" in source
    assert "CreateMutexW.restype" in source
    assert "if not mutex:" in source
    assert "will not start" in source


def test_launcher_and_desktop_shortcut_use_docmarshal_branding():
    root = Path(__file__).parents[1]
    launcher = (root / "launch_gui.pyw").read_text(encoding="utf-8")
    shortcut = (root / "install_desktop_shortcut.py").read_text(encoding="utf-8")

    assert "Local\\\\DocMarshal" in launcher
    assert '"DocMarshal"' in launcher
    assert "DocMarshal.lnk" in shortcut
    assert "docmarshal.ico" in shortcut
    assert "Little B's DOT Document Review.lnk" in shortcut
    assert "Launch DOT Document Review.lnk" in shortcut
    assert "ie4uinit.exe" in shortcut

import ctypes
from ctypes import wintypes
from pathlib import Path

APP_USER_MODEL_ID = "LittleBs.DocMarshal.Desktop"
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = (wintypes.LPCWSTR,)
shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
identity_result = shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)

from tkinter import messagebox

from dotdocs.gui import launch
from dotdocs.windows import focus_existing_window

ROOT = Path(__file__).resolve().parent
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL

ctypes.set_last_error(0)
mutex = kernel32.CreateMutexW(None, False, "Local\\DocMarshal")
mutex_error = ctypes.get_last_error()
if not mutex:
    messagebox.showerror(
        "DocMarshal",
        f"The single-instance safety lock could not be created (Windows error {mutex_error}). The program will not start.",
    )
    raise SystemExit(1)

try:
    if mutex_error == 183:
        if not focus_existing_window("DocMarshal"):
            messagebox.showinfo("DocMarshal", "DocMarshal is already running.")
    else:
        launch(ROOT / "config.json")
finally:
    kernel32.CloseHandle(mutex)

from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
launcher = root / "Launch DocMarshal.bat"
icon = root / "assets" / "docmarshal.ico"
if not launcher.exists():
    raise SystemExit(f"Launcher not found: {launcher}")
if not icon.exists():
    raise SystemExit(f"Icon not found: {icon}")

launcher_ps = str(launcher).replace("'", "''")
working_ps = str(root).replace("'", "''")
icon_ps = str(icon).replace("'", "''")
command = (
    "$desktop=[Environment]::GetFolderPath('Desktop');"
    "$legacy=Join-Path $desktop 'DOT Document Review.lnk';"
    "if(Test-Path $legacy){Remove-Item -LiteralPath $legacy -Force};"
    "$path=Join-Path $desktop 'DocMarshal.lnk';"
    "$shell=New-Object -ComObject WScript.Shell;"
    "$shortcut=$shell.CreateShortcut($path);"
    f"$shortcut.TargetPath='{launcher_ps}';"
    f"$shortcut.WorkingDirectory='{working_ps}';"
    "$shortcut.Description=\"DocMarshal fleet document review\";"
    f"$shortcut.IconLocation='{icon_ps},0';"
    "$shortcut.Save();"
    "Write-Output $path"
)
completed = subprocess.run(
    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
    check=True,
    capture_output=True,
    text=True,
)
print(completed.stdout.strip())

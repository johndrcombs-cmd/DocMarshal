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
legacy_shortcuts = [
    "DOT Document Review.lnk",
    "Little B's DOT Document Review.lnk",
    "Launch DOT Document Review.lnk",
]
legacy_ps = ",".join(f"'{name.replace(chr(39), chr(39) * 2)}'" for name in legacy_shortcuts)
command = (
    "$desktop=[Environment]::GetFolderPath('Desktop');"
    f"$legacyNames=@({legacy_ps});"
    "foreach($name in $legacyNames){"
    "$legacy=Join-Path $desktop $name;"
    "if(Test-Path $legacy){Remove-Item -LiteralPath $legacy -Force}"
    "};"
    "$path=Join-Path $desktop 'DocMarshal.lnk';"
    "if(Test-Path $path){Remove-Item -LiteralPath $path -Force};"
    "$shell=New-Object -ComObject WScript.Shell;"
    "$shortcut=$shell.CreateShortcut($path);"
    f"$shortcut.TargetPath='{launcher_ps}';"
    f"$shortcut.WorkingDirectory='{working_ps}';"
    "$shortcut.Description=\"DocMarshal fleet document review\";"
    f"$shortcut.IconLocation='{icon_ps},0';"
    "$shortcut.Save();"
    "$refresh=Join-Path $env:SystemRoot 'System32\\ie4uinit.exe';"
    "if(Test-Path $refresh){& $refresh -show | Out-Null};"
    "Write-Output $path"
)
completed = subprocess.run(
    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
    check=True,
    capture_output=True,
    text=True,
)
print(completed.stdout.strip())

@echo off
setlocal

REM Build BottomBot as a standalone Windows executable using PyInstaller.
REM Usage: double-click this file, or run from cmd/PowerShell.

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    set "PY_CMD=py"
) else (
    set "PY_CMD=python"
)

echo [1/4] Checking PyInstaller...
%PY_CMD% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    %PY_CMD% -m pip install --upgrade pyinstaller
    if errorlevel 1 (
        echo Failed to install PyInstaller.
        exit /b 1
    )
)


echo [2/4] Installing project dependencies...
%PY_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install project dependencies.
    exit /b 1
)

echo [3/4] Building executable...
%PY_CMD% -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --noconsole ^
  --name BottomBot ^
  --distpath dist ^
  --workpath build ^
  --hidden-import cv2 ^
  --hidden-import pynput ^
  --hidden-import numpy ^
  --hidden-import PyQt5 ^
  --hidden-import PySide6 ^
  --collect-submodules PySide6 ^
  --collect-submodules pynput ^
  main.py

if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo [4/4] Done.
echo Executable created at: dist\BottomBot.exe
endlocal

@echo off
setlocal

echo Building KestrelSAT GCS...

REM Always run from the folder this script is in.
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul

set "VENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
	echo ERROR: Virtual environment Python not found at:
	echo   %VENV_PYTHON%
	echo.
	echo Create the venv first, then install dependencies:
	echo   py -m venv .venv
	echo   .venv\Scripts\python -m pip install -r requirements.txt
	goto :fail
)

REM Ensure PyInstaller exists in the venv.
"%VENV_PYTHON%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
	echo PyInstaller not found in .venv. Installing it now...
	"%VENV_PYTHON%" -m pip install pyinstaller
	if errorlevel 1 goto :fail
)

REM Clean previous builds.
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM Build using the spec file.
"%VENV_PYTHON%" -m PyInstaller KestrelSAT_GCS_v1.spec
if errorlevel 1 goto :fail

echo.
echo Build complete! Executable is in the dist\ folder.
goto :done

:fail
echo.
echo Build failed.

:done
popd >nul
pause
endlocal

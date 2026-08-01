@echo off
echo Building KestrelSAT Ground Control Station...

REM Activate virtual environment
call .venv\Scripts\activate

REM Clean previous builds.
REM NOTE: do NOT delete *.spec here - KestrelSAT_GCS_v1.spec is checked in and
REM is the build definition this script uses.
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM Build the executable from the checked-in spec, so this script and
REM "pyinstaller KestrelSAT_GCS_v1.spec" produce the same artifact.
pyinstaller KestrelSAT_GCS_v1.spec

echo Build complete! Check the dist folder for KestrelSAT_GCS_v1.exe
pause

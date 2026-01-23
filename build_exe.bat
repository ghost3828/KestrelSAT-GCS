@echo off
echo Building Serial Communication GUI...

REM Activate virtual environment
call .venv\Scripts\activate

REM Clean previous builds
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist *.spec del *.spec

REM Build the executable
py pyinstaller --onefile --windowed --name "SerialGUI" ^
    --hidden-import "PyQt5.QtCore" ^
    --hidden-import "PyQt5.QtWidgets" ^
    --hidden-import "pyqtgraph" ^
    --hidden-import "serial.tools.list_ports" ^
    --collect-data "pyqtgraph" ^
    serial_gui.py

echo Build complete! Check the dist folder.
pause
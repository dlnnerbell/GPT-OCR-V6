@echo off
setlocal
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
if not exist "tesseract\tesseract.exe" (
  echo ERROR: tesseract\tesseract.exe was not found.
  echo Install Windows Tesseract and copy its entire folder here as .\tesseract
  exit /b 1
)

for /f "delims=" %%i in ('python -c "import pathlib,sys; print(pathlib.Path(sys.base_prefix)/'tcl'/'tcl8.6')"') do set "TCL_DATA=%%i"
for /f "delims=" %%i in ('python -c "import pathlib,sys; print(pathlib.Path(sys.base_prefix)/'tcl'/'tk8.6')"') do set "TK_DATA=%%i"

if not exist "%TCL_DATA%" (
  echo ERROR: Tcl runtime was not found: %TCL_DATA%
  exit /b 1
)
if not exist "%TK_DATA%" (
  echo ERROR: Tk runtime was not found: %TK_DATA%
  exit /b 1
)

python -m PyInstaller --noconfirm --clean --onefile --windowed --name "서류검증" ^
  --collect-all fitz ^
  --add-data "tesseract;tesseract" ^
  --add-data "%TCL_DATA%;_tcl_data" ^
  --add-data "%TK_DATA%;_tk_data" ^
  app.py
if errorlevel 1 exit /b 1
echo.
echo Build complete: dist\서류검증.exe

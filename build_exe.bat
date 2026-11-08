@echo off
REM Sestavi jednosouborove FionaDiag.exe do slozky dist\
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements-dev.txt || goto :err
python -m pytest -q || goto :err
for /f %%v in ('python -c "import fionadiag;print(fionadiag.__version__)"') do set VER=%%v
pyinstaller --noconfirm --onefile --windowed --name FionaDiag_v%VER% ^
  --add-data "fionadiag\data\fiona_responses.json;fionadiag\data" FionaDiag.pyw || goto :err
echo.
echo Hotovo: dist\FionaDiag_v%VER%.exe
exit /b 0
:err
echo CHYBA pri sestaveni.
exit /b 1

@echo off
rem Builds dist\Translator.exe (run this on Windows).
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat
pip install -r requirements.txt pyinstaller || goto :error
pyinstaller --noconfirm --onefile --windowed --name Translator --icon assets\icon.ico main.py || goto :error
echo.
echo Done: dist\Translator.exe
goto :eof

:error
echo Build failed.
exit /b 1

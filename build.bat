@echo off
chcp 65001 >nul
echo ==========================================
echo   Signing Helper - Build EXE
echo ==========================================
echo.
echo [1/3] Installing PyInstaller...
pip install pyinstaller
echo.
echo [2/3] Building EXE (this may take a minute)...
pyinstaller --onefile --noconsole --name SigningHelper --collect-all winocr signing_tool.py
if not exist "dist\SigningHelper.exe" goto failed
echo.
echo [3/3] Preparing share folder...
if exist "share" rmdir /s /q "share"
mkdir "share"
copy /y "dist\SigningHelper.exe" "share\" >nul
copy /y "rules.json" "share\" >nul
copy /y "phrases.json" "share\" >nul
copy /y "*.html" "share\" >nul 2>nul
echo.
echo ==========================================
echo   DONE!
echo   Send the whole "share" folder to your friend.
echo   (Friend must install zh-TW OCR first - see the guide.)
echo ==========================================
pause
exit /b 0
:failed
echo.
echo [FAILED] EXE was not created. Please send the messages above.
pause
exit /b 1

@echo off
setlocal
REM ============================================================
REM  Keyword Push - nikkan-auto-monitor
REM ------------------------------------------------------------
REM  WHAT THIS DOES:
REM    Commits ONLY keywords.json and pushes it to origin/main.
REM    GitHub Actions then picks up the new keyword list.
REM
REM  HOW TO USE:
REM    1. Edit keywords.json (add/remove keywords) and save.
REM    2. Double-click this .bat.
REM    3. Wait for "Done".
REM
REM  NOTES:
REM    - ASCII-only on purpose (avoids cmd.exe mojibake).
REM    - Only keywords.json is committed; other changed files
REM      are left untouched.
REM ============================================================
cd /d "%~dp0"

echo =========================================
echo  Nikkan Auto Monitor - Keyword Push
echo =========================================
echo.

REM --- Step 0: abort early if keywords.json has no changes ---
git diff --quiet -- keywords.json
if errorlevel 1 goto has_changes
echo [NO CHANGES] keywords.json is not modified.
echo Please edit keywords.json first.
goto end_error

:has_changes
REM --- Step 1/4: show exactly which file will be committed ---
echo [1/4] Changed file:
git status --short keywords.json
echo.

REM Build today's date (yyyy-MM-dd) for the commit message
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i

REM --- Step 2/4: stage ONLY keywords.json, then commit it ---
echo [2/4] Committing...
git add keywords.json
> "%TEMP%\nikkan_commitmsg.txt" echo keywords.json updated %TODAY%
git commit -F "%TEMP%\nikkan_commitmsg.txt"
del "%TEMP%\nikkan_commitmsg.txt" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Commit failed.
    goto end_error
)

echo.
REM --- Step 3/4: sync with remote first (rebase keeps history linear) ---
echo [3/4] Pull rebase from origin main...
git pull --rebase origin main
if errorlevel 1 (
    echo [ERROR] Pull failed. Uncommitted changes may block rebase.
    echo Try: git stash -u
    echo Then run this script again.
    goto end_error
)

echo.
REM --- Step 4/4: push to GitHub ---
echo [4/4] Push to origin main...
git push origin main
if errorlevel 1 (
    echo [ERROR] Push failed.
    goto end_error
)

echo.
echo =========================================
echo  Done. GitHub will use the new keywords.
echo =========================================
echo.
pause
exit /b 0

:end_error
echo.
pause
exit /b 1

@echo off
setlocal
REM ============================================================
REM  Keyword Push - nikkan-auto-monitor
REM ------------------------------------------------------------
REM  WHAT THIS DOES:
REM    Commits keywords.json (if edited) AND pushes any commits
REM    that are not on GitHub yet to origin/main.
REM
REM  HOW TO USE:
REM    1. Edit keywords.json (add/remove keywords) and SAVE it.
REM    2. Double-click this .bat.
REM    3. Wait for "Done".
REM
REM  WHY IT ALSO PUSHES UNPUSHED COMMITS:
REM    If you commit keywords.json from VS Code (or a previous
REM    push failed), the change is committed but NOT on GitHub.
REM    This script detects that and pushes it, so keywords never
REM    get stuck on your PC again.
REM
REM  NOTES:
REM    - ASCII-only on purpose (avoids cmd.exe mojibake).
REM    - Only keywords.json is staged; other changed files are
REM      left untouched.
REM ============================================================
cd /d "%~dp0"

echo =========================================
echo  Nikkan Auto Monitor - Keyword Push
echo =========================================
echo.

REM --- Step 1/4: commit keywords.json only if it has new edits ---
git diff --quiet -- keywords.json
if errorlevel 1 goto do_commit
echo [1/4] keywords.json has no new edits - checking GitHub...
goto check_remote

:do_commit
echo [1/4] keywords.json changed - validating JSON...
python -c "import json; json.load(open('keywords.json',encoding='utf-8')); print('  JSON OK')"
if errorlevel 1 (
    echo.
    echo [ERROR] keywords.json is NOT valid JSON.
    echo Common cause: a missing comma, or a trailing comma after the
    echo last keyword. Fix keywords.json, save, then run this again.
    goto end_error
)
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
echo   Committing keywords.json...
git add keywords.json
> "%TEMP%\nikkan_commitmsg.txt" echo keywords.json updated %TODAY%
git commit -F "%TEMP%\nikkan_commitmsg.txt"
del "%TEMP%\nikkan_commitmsg.txt" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Commit failed.
    goto end_error
)

:check_remote
echo.
REM --- Step 2/4: fetch and count commits not yet on GitHub ---
echo [2/4] Checking remote for unpushed commits...
git fetch origin main
for /f %%c in ('git rev-list --count origin/main..HEAD') do set UNPUSHED=%%c
if "%UNPUSHED%"=="0" goto nothing
echo   %UNPUSHED% commit(s) waiting to push.

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
echo  Done. GitHub now has the latest keywords.
echo  In the web app, press "reload" or refresh
echo  the page (F5) to see the new list.
echo =========================================
echo.
pause
exit /b 0

:nothing
echo.
echo [UP TO DATE] Nothing to push.
echo GitHub already has your latest keywords.
echo (If you just edited keywords.json, make sure you SAVED the file.)
echo.
pause
exit /b 0

:end_error
echo.
pause
exit /b 1

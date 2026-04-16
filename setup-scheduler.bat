@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ================================================
echo  日刊自動車新聞 サーバー自動起動 セットアップ
echo ================================================
echo.

:: 管理者権限チェック
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] このスクリプトは管理者権限で実行してください
    echo         右クリック → 「管理者として実行」
    pause
    exit /b 1
)

:: Python パス取得
for /f "delims=" %%A in ('where python') do set PYTHON_PATH=%%A
if "%PYTHON_PATH%"=="" (
    echo [ERROR] Python が見つかりません
    pause
    exit /b 1
)

echo Pythonパス: %PYTHON_PATH%
echo 作業ディレクトリ: %CD%
echo.

:: 既存タスク削除（エラーは無視）
echo [セットアップ] 既存タスクを削除中...
schtasks /delete /tn "nikkan-server" /f >nul 2>&1

:: 新規タスク作成
echo [セットアップ] タスクを作成中...
schtasks /create /tn "nikkan-server" /tr "%PYTHON_PATH% server.py" /sc onstart /ru SYSTEM /f >nul 2>&1

if %errorlevel% equ 0 (
    echo.
    echo ✅ セットアップ完了！
    echo.
    echo 次回PC起動時に server.py が自動で起動します。
    echo.
    echo 今すぐ起動したい場合:
    echo   python server.py
    echo.
) else (
    echo.
    echo ❌ セットアップに失敗しました
    pause
    exit /b 1
)

pause

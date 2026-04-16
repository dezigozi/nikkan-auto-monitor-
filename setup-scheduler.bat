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
    echo         右クリック ^→ 「管理者として実行」
    pause
    exit /b 1
)

echo [セットアップ] タスクスケジューラに登録中...
echo.

:: VBScript をタスクに登録（PC起動時に非表示で実行）
schtasks /create /tn "nikkan-server" /tr "wscript.exe \"%~dp0run-server-hidden.vbs\"" /sc onstart /ru SYSTEM /f

if %errorlevel% equ 0 (
    echo.
    echo ✅ セットアップ完了！
    echo.
    echo 次回 PC 起動時に server.py がバックグラウンドで自動起動します。
    echo.
    echo 確認コマンド:
    echo   schtasks /query /tn nikkan-server
    echo.
) else (
    echo.
    echo ❌ セットアップに失敗しました
    pause
    exit /b 1
)

pause

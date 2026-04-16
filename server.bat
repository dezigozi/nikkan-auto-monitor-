@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ================================================
echo  日刊自動車新聞 設定同期サーバー
echo ================================================
echo.

:: Flask をインストール（初回のみ）
python -c "import flask, flask_cors" 2>nul
if errorlevel 1 (
    echo [セットアップ] Flask をインストールします...
    pip install flask flask-cors
    echo.
)

echo サーバーを起動中...
echo.
python server.py

pause

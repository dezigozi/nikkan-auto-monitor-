@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ================================================
echo  日刊自動車新聞 トピックスモニター
echo ================================================

:: ---- 初回セットアップ（パッケージ未インストール時）----
python -c "import playwright" 2>nul
if errorlevel 1 (
    echo [セットアップ] 依存パッケージをインストールします...
    pip install -r requirements.txt
    python -m playwright install chromium
)

:: ---- OPENROUTER API KEY（未設定の場合はここに入力）----
:: set OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxx

:: ---- 実行 ----
echo.
python scraper.py

echo.
echo 完了しました。このウィンドウを閉じてください。
pause

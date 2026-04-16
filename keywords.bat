@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ================================================
echo  キーワード管理ツール
echo ================================================
echo.
echo 操作を選んでください:
echo   1. 一覧表示
echo   2. キーワードを追加
echo   3. キーワードを削除
echo   4. キーワードを変更
echo   5. 終了
echo.
set /p CHOICE="番号を入力してください (1-5): "

if "%CHOICE%"=="1" goto LIST
if "%CHOICE%"=="2" goto ADD
if "%CHOICE%"=="3" goto REMOVE
if "%CHOICE%"=="4" goto EDIT
if "%CHOICE%"=="5" goto END
echo 無効な番号です
pause
goto END

:LIST
python manage_keywords.py list
pause
goto END

:ADD
echo.
set /p WORD="追加するキーワードを入力してください: "
python manage_keywords.py add "%WORD%"
pause
goto END

:REMOVE
echo.
python manage_keywords.py list
set /p WORD="削除するキーワードを入力してください: "
python manage_keywords.py remove "%WORD%"
pause
goto END

:EDIT
echo.
python manage_keywords.py list
set /p OLD_WORD="変更前のキーワードを入力してください: "
set /p NEW_WORD="変更後のキーワードを入力してください: "
python manage_keywords.py edit "%OLD_WORD%" "%NEW_WORD%"
pause
goto END

:END

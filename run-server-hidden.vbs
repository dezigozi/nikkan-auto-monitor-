' サーバーをバックグラウンド（非表示）で実行
Set objShell = CreateObject("WScript.Shell")
strPath = objShell.CurrentDirectory
objShell.Run "python server.py", 0, False

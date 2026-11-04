Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
' Start Streamlit silently without opening a standard browser tab
WshShell.Run """C:\Program Files\Python312\python.exe"" -m streamlit run app.py --server.headless true --server.port 8501", 0, False
WScript.Sleep 3000
' Open PayTracker in a standalone frameless desktop app window
WshShell.Run "msedge.exe --app=http://localhost:8501 --window-size=1280,820", 1, False
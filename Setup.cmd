@echo off
winget install --exact --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
winget install --exact --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
winget install --exact --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
echo Close this window, open a new terminal in this folder, and run:
echo py -3 -m pip install -r requirements.txt
echo Then double-click Start.cmd.
pause

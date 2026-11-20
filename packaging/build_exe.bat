@echo off
set WINDIR=C:\windows
set PATH=C:\windows\system32;C:\windows;C:\py;C:\py\Scripts
cd /d Z:\src
Z:\py\python.exe -m PyInstaller packaging\srm_open.spec --noconfirm --distpath dist --workpath build\pyi

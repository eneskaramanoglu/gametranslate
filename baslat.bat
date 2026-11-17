@echo off
rem Overlay uygulamasini baslatir (uv gerekmez; projenin .venv ortamini kullanir).
cd /d "%~dp0"
if not exist ".venv\Scripts\gta-tr-subs.exe" (
    echo .venv bulunamadi. Once kurulum yapin: README.md - Kurulum
    pause
    exit /b 1
)
".venv\Scripts\gta-tr-subs.exe" %*
if errorlevel 1 pause

@echo off
title NATA® Dashboard Launcher
color 0A

echo.
echo  ==========================================
echo   NATA® Dashboard - Iniciando...
echo  ==========================================
echo.

echo  [1/2] Iniciando servidor web...
start "NATA Dashboard" cmd /k "cd /d "%~dp0" && python dashboard.py"
timeout /t 3 /nobreak > nul

echo  [2/2] Abrindo tunel...
echo.
echo  ==========================================
echo   URL PERMANENTE DO DASHBOARD:
echo   https://21473d90e2694b.lhr.life
echo  ==========================================
echo.
echo  Mantenha esta janela aberta!
echo  Fechar = dashboard offline para outros.
echo.

ssh -o StrictHostKeyChecking=no -R 80:localhost:5500 ssh.localhost.run

echo.
echo  Tunel encerrado.
pause

@echo off
title HypeBot Deploy Railway
cd /d "C:\Users\Anderson Chaves\Downloads\bot"
color 0A
echo.
echo  ====================================
echo   HypeBot - Deploy 24/7 no Railway
echo  ====================================
echo.
echo  PASSO 1: Login (o navegador vai abrir)
echo  Clique em AUTHORIZE no navegador!
echo.
railway login
echo.
echo  PASSO 2: Criando projeto...
railway init --name hypebot
echo.
echo  PASSO 3: Configurando variavel...
railway variables --set "DATA_DIR=/data"
echo.
echo  PASSO 4: Fazendo deploy (aguarde...)
railway up --detach
echo.
echo  PASSO 5: Gerando URL publica...
railway domain
echo.
echo  ====================================
echo   PRONTO! Site online 24/7!
echo  ====================================
echo.
pause

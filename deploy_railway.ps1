# HypeBot — Deploy automatico no Railway
# Unico passo manual: clicar "Authorize" no navegador que vai abrir

$Host.UI.RawUI.WindowTitle = "HypeBot Deploy"
Set-Location "C:\Users\Anderson Chaves\Downloads\bot"

function Write-Step($msg) {
    Write-Host ""
    Write-Host ">>> $msg" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "    OK: $msg" -ForegroundColor Green
}

function Write-Fail($msg) {
    Write-Host "    ERRO: $msg" -ForegroundColor Red
}

# ── 1. Login ──────────────────────────────────────────────────────────────────
Write-Step "PASSO 1/5 — Login no Railway"
Write-Host "    O navegador vai abrir. Clique em AUTHORIZE e volte aqui." -ForegroundColor Yellow
railway login
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Login falhou. Tente executar este script novamente."
    Read-Host "Pressione Enter para fechar"
    exit 1
}
Write-OK "Logado com sucesso!"

# ── 2. Criar projeto ─────────────────────────────────────────────────────────
Write-Step "PASSO 2/5 — Criando projeto HypeBot"
$initOutput = railway init --name hypebot 2>&1
Write-Host "    $initOutput"
Start-Sleep -Seconds 2

# ── 3. Variavel DATA_DIR ─────────────────────────────────────────────────────
Write-Step "PASSO 3/5 — Configurando variavel DATA_DIR=/data"
railway variables --set "DATA_DIR=/data" 2>&1
Write-OK "Variavel configurada"
Start-Sleep -Seconds 1

# ── 4. Deploy ────────────────────────────────────────────────────────────────
Write-Step "PASSO 4/5 — Fazendo upload dos arquivos (aguarde...)"
railway up --detach 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Deploy falhou. Verifique os erros acima."
    Read-Host "Pressione Enter para fechar"
    exit 1
}
Write-OK "Arquivos enviados para o Railway!"
Start-Sleep -Seconds 3

# ── 5. Gerar dominio publico ─────────────────────────────────────────────────
Write-Step "PASSO 5/5 — Gerando URL publica"
$domainOutput = railway domain 2>&1
Write-Host "    $domainOutput"

# Extrair a URL do output
$url = $domainOutput | Select-String -Pattern "https://\S+" | ForEach-Object { $_.Matches[0].Value }

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  DEPLOY CONCLUIDO!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
if ($url) {
    Write-Host "  Site online 24/7 em:" -ForegroundColor Green
    Write-Host "  $url" -ForegroundColor Yellow
} else {
    Write-Host "  Acesse railway.app para ver sua URL" -ForegroundColor Yellow
}
Write-Host "  O site roda mesmo com o PC desligado!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  IMPORTANTE: Va em railway.app -> seu projeto ->" -ForegroundColor White
Write-Host "  Settings -> Networking -> Generate Domain" -ForegroundColor White
Write-Host "  (se a URL nao apareceu acima)" -ForegroundColor White
Write-Host ""

Read-Host "Pressione Enter para fechar"

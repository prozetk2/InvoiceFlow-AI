<#
.SYNOPSIS
  Publish InvoiceFlow AI to a public GitHub repository owned by prozetk2.
.DESCRIPTION
  Requires Git and GitHub CLI (gh). Signs in via `gh auth login` first.
  Refuses to overwrite an existing GitHub repository or stage known secret/data file types.
  Run at the project root: powershell -ExecutionPolicy Bypass -File scripts/publish_github.ps1
#>
$ErrorActionPreference = 'Stop'
$Repo = 'prozetk2/invoiceflow-ai'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

foreach ($tool in @('git', 'gh')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "Missing '$tool'. Install Git and GitHub CLI before publishing."
    }
}

gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'GitHub CLI is not authenticated. Run: gh auth login'
}

$login = (gh api user --jq .login).Trim()
if ($LASTEXITCODE -ne 0 -or $login -ne 'prozetk2') {
    throw "Wrong GitHub account '$login'; expected 'prozetk2'."
}

gh repo view $Repo --json name *> $null
if ($LASTEXITCODE -eq 0) {
    throw "Repository $Repo already exists. Refusing to overwrite it."
}

Push-Location $ProjectRoot
try {
    if (-not (Test-Path '.git')) {
        git init -b main
        if ($LASTEXITCODE -ne 0) { throw 'git init failed' }
    }
    git add --all
    if ($LASTEXITCODE -ne 0) { throw 'git add failed' }

    $staged = @(git diff --cached --name-only)
    if (-not $staged -or $staged.Count -eq 0) {
        throw 'No files staged; nothing to publish.'
    }

    # Fail closed on common credentials, health records and local runtime artifacts.
    $blocked = @($staged | Where-Object {
        $_ -match '(^|/)(\.env(\.|$)|secrets\.json$|data/[^/]+$)' -or
        $_ -match '(?i)\.(pem|key|p12|sqlite3?|db|png|jpe?g|webp|pdf|xlsx?|csv|parquet|zip)$'
    })
    if ($blocked.Count -gt 0) {
        Write-Host 'BLOCKED: sensitive/large staged files detected:' -ForegroundColor Red
        $blocked | ForEach-Object { Write-Host "  $_" }
        throw 'Review staged files before publishing.'
    }

    Write-Host "Publishing $($staged.Count) files to PUBLIC repository $Repo" -ForegroundColor Cyan
    $staged | ForEach-Object { Write-Host "  $_" }
    $confirm = Read-Host "Type PUBLISH to confirm PUBLIC publication"
    if ($confirm -cne 'PUBLISH') { throw 'Cancelled by user.' }

    git commit -m 'feat: release InvoiceFlow AI portfolio baseline'
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed. Set git user.name and user.email.' }

    gh repo create $Repo --public --description 'InvoiceFlow AI | Vietnamese OCR, KIE, human review and RPA handoff' --source . --remote origin --push
    if ($LASTEXITCODE -ne 0) { throw 'GitHub publication failed; inspect gh output.' }

    Write-Host "Published: https://github.com/$Repo" -ForegroundColor Green
}
finally {
    Pop-Location
}

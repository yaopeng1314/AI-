$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

function Invoke-Git {
    git @args
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed: git $args"
    }
}

$safeDirectory = (Resolve-Path -Path $PSScriptRoot).Path.Replace("\", "/")

Invoke-Git config --global --add safe.directory $safeDirectory
Invoke-Git remote set-url origin https://github.com/yaopeng1314/AI-.git
Invoke-Git branch -M main
Invoke-Git config http.proxy http://127.0.0.1:15236
Invoke-Git config https.proxy http://127.0.0.1:15236
Invoke-Git config http.sslBackend openssl

Write-Host "Pushing insider-buy monitor to https://github.com/yaopeng1314/AI- ..."
Write-Host "This will replace the placeholder README currently in that repository."

Invoke-Git push -u origin main --force

Write-Host ""
Write-Host "Done. Next: open GitHub Actions in the repository and enable workflow write permissions if GitHub asks."

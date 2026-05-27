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

Write-Host "Pushing insider-buy monitor to https://github.com/yaopeng1314/AI- ..."
Write-Host "This will replace the placeholder README currently in that repository."

Invoke-Git push -u origin main --force

Write-Host ""
Write-Host "Done. Next: open GitHub Actions in the repository and enable workflow write permissions if GitHub asks."

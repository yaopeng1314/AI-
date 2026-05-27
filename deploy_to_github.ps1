$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

git remote set-url origin https://github.com/yaopeng1314/AI-.git
git branch -M main

Write-Host "Pushing insider-buy monitor to https://github.com/yaopeng1314/AI- ..."
Write-Host "This will replace the placeholder README currently in that repository."

git push -u origin main --force

Write-Host ""
Write-Host "Done. Next: open GitHub Actions in the repository and enable workflow write permissions if GitHub asks."

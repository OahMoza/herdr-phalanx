$shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) {
    (Get-Command pwsh).Source
} else {
    "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
}
Write-Output $shell

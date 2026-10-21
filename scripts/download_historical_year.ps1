# Download and convert one calendar year of CODE Final GIM to the shared NPZ database.
# Run from any directory: .\scripts\download_historical_year.ps1 -Year 2002
# Existing IONEX files and NPZ outputs are never overwritten by the Python tools.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1999, 2021)]
    [int]$Year,

    [string]$Email,

    [ValidateRange(1, 10)]
    [int]$MaxRepairRounds = 3
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$downloader = Join-Path $repoRoot "cddis_gim_ftps_downloader.py"
$reader = Join-Path $repoRoot "ionex_reader.py"
$inputDir = Join-Path $repoRoot "gim_data\$Year"
$outputDir = Join-Path $repoRoot "processed"
$startDate = "{0}-01-01" -f $Year
$endDate = "{0}-12-31" -f $Year

if (-not (Test-Path -LiteralPath $downloader -PathType Leaf) -or
    -not (Test-Path -LiteralPath $reader -PathType Leaf)) {
    throw "Run this script from the gim-data-tools repository (scripts/ directory)."
}

if ([string]::IsNullOrWhiteSpace($Email)) {
    $Email = Read-Host "CDDIS anonymous email"
}
if ([string]::IsNullOrWhiteSpace($Email)) {
    throw "CDDIS anonymous email is required."
}

function Get-MissingDates {
    param(
        [int]$TargetYear,
        [string]$ProcessedDirectory
    )

    $missing = @(
        for ($day = [datetime]::new($TargetYear, 1, 1);
             $day.Year -eq $TargetYear;
             $day = $day.AddDays(1)) {

            $filename = "codg{0:000}0.{1:00}i.npz" -f $day.DayOfYear, ($TargetYear % 100)
            if (-not (Test-Path -LiteralPath (Join-Path $ProcessedDirectory $filename) -PathType Leaf)) {
                $day.ToString("yyyy-MM-dd")
            }
        }
    )
    return $missing
}

function Convert-DownloadedIONEX {
    & python $reader --input-dir $inputDir --output-dir $outputDir
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Warning "Batch conversion returned exit code $code; checking missing NPZ dates."
    }
}

New-Item -ItemType Directory -Force -Path $inputDir, $outputDir | Out-Null

$missing = @(Get-MissingDates -TargetYear $Year -ProcessedDirectory $outputDir)
if ($missing.Count -eq 0) {
    Write-Host "SUCCESS: $Year already complete (all expected NPZ files exist)."
    return
}

Write-Host "Year $Year: $($missing.Count) NPZ dates missing before download."
Write-Host "Downloading CODE FIN products for $startDate through $endDate ..."
& python $downloader --email $Email --start-date $startDate --end-date $endDate --agency COD --type FIN --output-folder $inputDir
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Warning "Annual download returned exit code $code; trying conversion and targeted repairs."
}

Convert-DownloadedIONEX

for ($round = 1; $round -le $MaxRepairRounds; $round++) {
    $missing = @(Get-MissingDates -TargetYear $Year -ProcessedDirectory $outputDir)
    if ($missing.Count -eq 0) { break }

    Write-Host "Repair round $round/$MaxRepairRounds : $($missing.Count) missing day(s)."

    foreach ($date in $missing) {
        Write-Host "Retrying $date"
        & python $downloader --email $Email --start-date $date --end-date $date --agency COD --type FIN --output-folder $inputDir
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            Write-Warning "Download of $date returned exit code $code."
        }
    }

    Convert-DownloadedIONEX
}

$missing = @(Get-MissingDates -TargetYear $Year -ProcessedDirectory $outputDir)
$expectedDays = ([datetime]::new($Year + 1, 1, 1) - [datetime]::new($Year, 1, 1)).Days
if ($missing.Count -gt 0) {
    Write-Host "Missing dates for $Year :"
    $missing | ForEach-Object { Write-Host "  $_" }
    throw "$Year incomplete: $($missing.Count)/$expectedDays daily NPZ files missing after $MaxRepairRounds repair rounds."
}

Write-Host "SUCCESS: $Year complete ($expectedDays/$expectedDays daily NPZ filenames present)."
Write-Host "Note: file presence does not prove scientific contents or epoch validity."

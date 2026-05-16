﻿# V2rayN Node Import Script
# Tests node sources, auto-discovers replacements if dead, fetches nodes, imports to V2rayN
$ErrorActionPreference = "SilentlyContinue"
$ProgressPreference = "SilentlyContinue"

Write-Host ""
Write-Host "===== V2rayN Node Import =====" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# Source config
# ============================================================
$sources = @(
    @{name="V2RayAggregator"; url="https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/sub_merge.txt"; enc="plain"},
    @{name="ermaozi";         url="https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/v2ray.txt"; enc="base64"},
    @{name="ssrsub";          url="https://raw.githubusercontent.com/ssrsub/ssr/master/v2ray"; enc="base64"},
    @{name="ripaojiedian";    url="https://raw.githubusercontent.com/ripaojiedian/freenode/main/sub"; enc="base64"},
    @{name="NoMoreWalls";     url="https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.txt"; enc="base64"},
    @{name="mfuu";            url="https://raw.githubusercontent.com/mfuu/v2ray/master/v2ray"; enc="base64"},
    @{name="Pawdroid";        url="https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub"; enc="base64"}
)

$mirrors = @("https://gh-proxy.com/", "https://ghps.cc/")
$validSchemes = "vmess","vless","ss","ssr","trojan","hysteria2","hysteria","tuic"

# Candidate filenames for auto-discovery
$candidateFiles = @("v2ray.txt","v2ray","sub","sub.txt","nodes.txt","nodes","list.txt","sub_merge.txt","vmess.txt")

# ============================================================
# Functions
# ============================================================
function Test-Url([string]$url, [int]$timeout=5) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeout
        return $r.Content.Trim().Length -gt 10
    } catch { return $false }
}

function Test-Source([hashtable]$src) {
    $ok = Test-Url $src.url 6
    if (-not $ok -and $src.url -like "*raw.githubusercontent.com*") {
        foreach ($m in $mirrors) {
            if (Test-Url ($m + $src.url) 6) { return $true }
        }
    }
    return $ok
}

function Discover-NewSources([System.Collections.ArrayList]$known) {
    Write-Host "  Searching GitHub for new sources..." -ForegroundColor Yellow

    # Search GitHub API
    $apiUrl = "https://api.github.com/search/repositories?q=free+v2ray+nodes+in:name&sort=updated&order=desc&per_page=5"
    try {
        $data = Invoke-RestMethod -Uri $apiUrl -TimeoutSec 8
    } catch {
        Write-Host "  GitHub API unavailable" -ForegroundColor Red
        return @()
    }

    $newSources = @()
    foreach ($item in $data.items) {
        if ($newSources.Count -ge 2) { break }
        if ($item.pushed_at -lt "2026-02-01") { continue }

        $repo = $item.full_name
        foreach ($fname in $candidateFiles) {
            foreach ($branch in @("main","master")) {
                $url = "https://raw.githubusercontent.com/$repo/$branch/$fname"
                if ($url -in $known) { continue }
                if (Test-Url $url 6) {
                    $name = $repo.Split("/")[1]
                    Write-Host "  + NEW: $name ($fname)" -ForegroundColor Green
                    $newSources += @{name=$name; url=$url; enc="base64"}
                    break
                }
            }
            if ($newSources.Count -ge 2) { break }
        }
    }
    return $newSources
}

# ============================================================
# 1. Health check (concurrent)
# ============================================================
Write-Host "[1/4] Health check..." -ForegroundColor Yellow

$jobs = @()
foreach ($src in $sources) {
    # V2RayAggregator is large (1.2MB) and reliable — skip health check
    if ($src.name -eq "V2RayAggregator") { continue }

    $jobs += [PSCustomObject]@{
        src  = $src
        job  = Start-Job -ScriptBlock {
            param($url, $mirrors)
            try {
                $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 8
                if ($r.Content.Trim().Length -gt 10) { return $true }
            } catch {}
            foreach ($m in $mirrors) {
                try {
                    $r = Invoke-WebRequest -Uri ($m + $url) -UseBasicParsing -TimeoutSec 8
                    if ($r.Content.Trim().Length -gt 10) { return $true }
                } catch {}
            }
            return $false
        } -ArgumentList $src.url, $mirrors
    }
}

# Wait for all jobs (max 12 sec)
$null = $jobs | ForEach-Object { $_.job | Wait-Job -Timeout 12 }

$aliveSources = @()
# Always keep V2RayAggregator
$aliveSources += ($sources | Where-Object { $_.name -eq "V2RayAggregator" })
$deadNames = @()
$knownUrls = $sources.url

foreach ($j in $jobs) {
    $result = $j.job | Receive-Job -ErrorAction SilentlyContinue
    $j.job | Remove-Job -Force -ErrorAction SilentlyContinue
    if ($result -eq $true) {
        Write-Host ("  OK   " + $j.src.name) -ForegroundColor Green
        $aliveSources += $j.src
    } else {
        Write-Host ("  DEAD " + $j.src.name) -ForegroundColor Red
        $deadNames += $j.src.name
    }
}

# Auto-discover replacements for dead sources
if ($deadNames.Count -gt 0) {
    Write-Host ("  {0} dead source(s): {1}" -f $deadNames.Count, ($deadNames -join ", ")) -ForegroundColor Yellow
    $replacements = Discover-NewSources $knownUrls
    if ($replacements.Count -gt 0) {
        Write-Host ("  Added {0} new source(s)" -f $replacements.Count) -ForegroundColor Green
        $aliveSources += $replacements
    }
}

if ($aliveSources.Count -eq 0) {
    Write-Host "No available sources!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    return
}

Write-Host ("  {0} source(s) ready" -f $aliveSources.Count) -ForegroundColor Green

# ============================================================
# 2. Fetch nodes
# ============================================================
Write-Host ""
Write-Host "[2/4] Fetching nodes..." -ForegroundColor Yellow

$allNodes = [System.Collections.ArrayList]::new()
foreach ($src in $aliveSources) {
    Write-Host ("  {0} ... " -f $src.name) -NoNewline
    $raw = $null

    # Try primary URL
    try {
        $raw = (Invoke-WebRequest -Uri $src.url -UseBasicParsing -TimeoutSec 15).Content.Trim()
    } catch {}

    # Try mirrors if primary failed or empty
    if (($raw -eq $null -or $raw.Length -lt 10) -and $src.url -like "*raw.githubusercontent.com*") {
        foreach ($m in $mirrors) {
            try {
                $raw = (Invoke-WebRequest -Uri ($m + $src.url) -UseBasicParsing -TimeoutSec 10).Content.Trim()
                if ($raw.Length -gt 10) { break }
            } catch {}
        }
    }

    if ($raw -eq $null -or $raw.Length -lt 10) { Write-Host "failed"; continue }

    if ($src.enc -eq "base64") {
        $raw = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($raw))
    }
    $count = 0
    foreach ($line in ($raw -split "`r?`n")) {
        $t = $line.Trim()
        $scheme = if ($t -match "://") { $t.Split("://")[0].ToLower() } else { "" }
        if ($scheme -in $validSchemes) {
            [void]$allNodes.Add($t)
            $count++
        }
    }
    Write-Host "$count nodes" -ForegroundColor Gray
}

if ($allNodes.Count -eq 0) {
    Write-Host "`nNo nodes collected!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    return
}

# Deduplicate
$seen = [System.Collections.Generic.HashSet[string]]::new()
$unique = [System.Collections.ArrayList]::new()
foreach ($n in $allNodes) { if ($seen.Add($n)) { [void]$unique.Add($n) } }

Write-Host ""
Write-Host ("  Total: {0} nodes (deduped from {1})" -f $unique.Count, $allNodes.Count) -ForegroundColor Green

# ============================================================
# 3. Copy to clipboard
# ============================================================
Write-Host ""
Write-Host "[3/4] Copying to clipboard..." -ForegroundColor Yellow

$tempFile = Join-Path $env:TEMP "v2rayn_nodes.txt"
[System.IO.File]::WriteAllText($tempFile, ($unique -join "`r`n"), [System.Text.Encoding]::UTF8)
$clipExe = Join-Path $env:SystemRoot "System32\clip.exe"
& cmd.exe /c ('type "{0}" | "{1}"' -f $tempFile, $clipExe)
Remove-Item $tempFile -ErrorAction SilentlyContinue
Write-Host "  Copied to clipboard" -ForegroundColor Green

# ============================================================
# 4. Launch V2rayN
# ============================================================
Write-Host ""
Write-Host "[4/4] Launching V2rayN..." -ForegroundColor Yellow

$v2raynExe = "E:\zm\压缩包\v2rayN-windows-64\v2rayN-windows-64\v2rayN.exe"
if (Test-Path $v2raynExe) {
    Start-Process -FilePath $v2raynExe
    Write-Host "  V2rayN started" -ForegroundColor Green
} else {
    Write-Host ("  Not found: {0}" -f $v2raynExe) -ForegroundColor Red
}

Write-Host ""
Write-Host "Next: Right-click V2rayN tray icon -> Server -> Import from clipboard" -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to exit"

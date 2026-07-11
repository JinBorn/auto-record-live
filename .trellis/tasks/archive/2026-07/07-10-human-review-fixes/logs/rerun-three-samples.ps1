$ErrorActionPreference = 'Stop'
$env:ARL_POSTPROCESS_PRESET = 'publish'
$env:ARL_LLM_STORY_ANALYSIS_ENABLED = '1'
$env:ARL_LLM_STORY_SHADOW_MODE = '0'

$python = '.\.venv\Scripts\python.exe'
$samples = @(
    @{ Session = 'session-20260617073649-4b5ec478'; Match = '2' },
    @{ Session = 'session-20260617073651-cf11bf9e'; Match = '3' },
    @{ Session = 'session-20260702092321-bc90812b'; Match = '1' }
)

foreach ($sample in $samples) {
    $session = $sample.Session
    $match = $sample.Match
    Write-Output "=== SAMPLE START session=$session match=$match ==="
    & $python -m arl.cli subtitles --session-id $session --match-index $match --force-reprocess
    if ($LASTEXITCODE -ne 0) { throw "subtitles failed session=$session match=$match" }
    & $python -m arl.cli highlight-planner --session-id $session --match-index $match --force-reprocess
    if ($LASTEXITCODE -ne 0) { throw "highlight-planner failed session=$session match=$match" }
    & $python -m arl.cli copywriter --session-id $session --match-index $match --force-reprocess
    if ($LASTEXITCODE -ne 0) { throw "copywriter semantic failed session=$session match=$match" }
    & $python -m arl.cli edit-planner --session-id $session --match-index $match --force-reprocess
    if ($LASTEXITCODE -ne 0) { throw "edit-planner failed session=$session match=$match" }
    & $python -m arl.cli exporter --session-id $session --match-index $match --force-reprocess
    if ($LASTEXITCODE -ne 0) { throw "exporter failed session=$session match=$match" }
    & $python -m arl.cli copywriter --session-id $session --match-index $match --force-reprocess
    if ($LASTEXITCODE -ne 0) { throw "copywriter publishing failed session=$session match=$match" }
    & $python -m arl.cli quality-report --session-id $session --match-index $match
    if ($LASTEXITCODE -ne 0) { throw "quality-report failed session=$session match=$match" }
    Write-Output "=== SAMPLE DONE session=$session match=$match ==="
}

Write-Output '=== ALL SAMPLES DONE ==='

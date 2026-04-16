param(
    [switch]$SkipBuild,
    [switch]$SkipFailover,
    [switch]$SkipBonus
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$apiUrl = "http://127.0.0.1:8081/checkout"
$dbServices = @("books_database_1", "books_database_2", "books_database_3")
$executorServices = @("order_executor_1", "order_executor_2", "order_executor_3")
$logServices = @(
    "orchestrator",
    "payment_service",
    "order_queue"
) + $executorServices + $dbServices
$pythonFiles = @(
    "books_database/src/app.py",
    "payment_service/src/app.py",
    "order_executor/src/app.py",
    "orchestrator/src/app.py"
)
$results = [System.Collections.Generic.List[object]]::new()
$script:CurrentFailureList = $null

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Add-CheckResult {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Details
    )

    $results.Add([pscustomobject]@{
        Name = $Name
        Passed = $Passed
        Details = $Details
    })

    $status = if ($Passed) { "PASS" } else { "FAIL" }
    Write-Host ("[{0}] {1} - {2}" -f $status, $Name, $Details)
}

function Run-Compose {
    param([string[]]$ComposeArgs)

    # Docker compose prints container-status lines to stderr. Capturing with
    # 2>&1 under $ErrorActionPreference='Stop' would turn those lines into
    # terminating errors, so suppress stderr for this call only and rely on
    # the exit code instead.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & docker compose @ComposeArgs 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prev
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = ($output | Out-String).TrimEnd()
    }
}

function Get-ComposeLogs {
    param(
        [string[]]$Services,
        [int]$Tail = 400,
        [string]$Since
    )

    $logArgs = @("logs", "--no-color")
    if ($Since) {
        $logArgs += @("--since", $Since)
    }
    $logArgs += "--tail=$Tail"
    $logArgs += $Services

    $result = Run-Compose $logArgs
    if ($result.ExitCode -ne 0) {
        throw "docker compose logs failed.`n$($result.Output)"
    }

    return $result.Output
}

function Invoke-Checkout {
    param([string]$FilePath)

    # Invoke-WebRequest has intermittent NPE issues against our orchestrator,
    # so shell out to Python which handles the HTTP dance cleanly.
    $absPath = (Resolve-Path $FilePath).Path
    $pyCode = @"
import json, sys, urllib.request
with open(r'$absPath', 'rb') as f:
    body = f.read()
req = urllib.request.Request(
    '$apiUrl', data=body,
    headers={'Content-Type': 'application/json'}, method='POST')
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        code = resp.status
        text = resp.read().decode('utf-8')
except urllib.error.HTTPError as e:
    code = e.code
    text = e.read().decode('utf-8')
print(code)
print(text)
"@
    $out = & python -c $pyCode
    if ($LASTEXITCODE -ne 0) {
        throw "checkout POST failed: $($out | Out-String)"
    }
    $lines = $out -split "\r?\n", 2
    $code = [int]$lines[0]
    $body = if ($lines.Count -gt 1) { $lines[1] } else { "" }
    return [pscustomobject]@{
        StatusCode = $code
        Json = ($body | ConvertFrom-Json)
        Raw = $body
    }
}

function Wait-ForOrchestrator {
    $pyCode = @"
import sys, time, urllib.request
deadline = time.time() + 60
while time.time() < deadline:
    try:
        with urllib.request.urlopen('http://127.0.0.1:8081/', timeout=3) as r:
            if r.status == 200:
                sys.exit(0)
    except Exception:
        pass
    time.sleep(2)
sys.exit(1)
"@
    & python -c $pyCode | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Orchestrator did not become ready on http://127.0.0.1:8081/."
    }
}

function Wait-ForDbPrimary {
    param([int]$TimeoutSeconds = 40)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $out = & python scripts/_cp3_db_probe.py find-primary 2>$null
        if ($LASTEXITCODE -eq 0) {
            $text = ($out | Out-String)
            $m = [regex]::Match($text, "primary_id=(\d+)")
            if ($m.Success) {
                return [int]$m.Groups[1].Value
            }
        }
        Start-Sleep -Seconds 2
    }
    throw "No DB primary was elected within ${TimeoutSeconds}s."
}

function Assert-Condition {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        if ($null -eq $script:CurrentFailureList) {
            throw "Current failure list is not initialized."
        }
        $script:CurrentFailureList.Add($Message)
    }
}

function Get-OrderLogLines {
    param(
        [string]$Logs,
        [string]$OrderId
    )

    $normalizedOrderId = $OrderId.Trim()

    return @(
        ($Logs -split "\r?\n") | Where-Object { $_ -like "*$normalizedOrderId*" }
    )
}

function Read-StockOnAllReplicas {
    param(
        [string]$Title,
        [switch]$TolerateMissing
    )

    $probeArgs = @("scripts/_cp3_db_probe.py", "read-stock", $Title)
    if ($TolerateMissing) { $probeArgs += "--tolerate-missing" }
    $out = & python @probeArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "read-stock failed for title '$Title':`n$out"
    }
    $map = @{}
    foreach ($line in ($out -split "\r?\n")) {
        $m = [regex]::Match($line, "^DB-(\d+)=(.+)$")
        if ($m.Success) {
            $rid = [int]$m.Groups[1].Value
            $val = $m.Groups[2].Value
            if ($val -match "^-?\d+$") {
                $map[$rid] = [int]$val
            }
            else {
                $map[$rid] = $val
            }
        }
    }
    return $map
}

function Wait-For2pcOutcome {
    param(
        [string]$OrderId,
        [string]$ExpectedDecision,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $latestLogs = ""
    while ((Get-Date) -lt $deadline) {
        $latestLogs = Get-ComposeLogs -Services $logServices -Tail 1500 -Since "5m"
        $orderLines = Get-OrderLogLines -Logs $latestLogs -OrderId $OrderId
        $decisionLines = @($orderLines | Where-Object {
            $_ -match "2pc_decision" -and $_ -match "decision=$ExpectedDecision"
        })
        if ($ExpectedDecision -eq "COMMIT") {
            $applied = @($orderLines | Where-Object { $_ -match "2pc_commit_applied" })
            if ($decisionLines.Count -ge 1 -and $applied.Count -ge 1) {
                return $latestLogs
            }
        }
        else {
            $aborted = @($orderLines | Where-Object { $_ -match "2pc_abort_applied" })
            if ($decisionLines.Count -ge 1 -and $aborted.Count -ge 1) {
                return $latestLogs
            }
        }
        Start-Sleep -Seconds 1
    }
    return $latestLogs
}

function Test-ValidCommit {
    $failures = [System.Collections.Generic.List[string]]::new()
    $script:CurrentFailureList = $failures

    $title = "Book A"
    $before = Read-StockOnAllReplicas -Title $title
    $response = Invoke-Checkout -FilePath "test_checkout.json"
    $orderId = ([string]$response.Json.orderId).Trim()
    $logs = Wait-For2pcOutcome -OrderId $orderId -ExpectedDecision "COMMIT"
    $orderLines = Get-OrderLogLines -Logs $logs -OrderId $orderId

    Assert-Condition ($response.StatusCode -eq 200) "Expected HTTP 200 but got $($response.StatusCode)."
    Assert-Condition ("Order Approved" -eq [string]$response.Json.status) "Expected 'Order Approved' but got '$($response.Json.status)'."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[PAYMENT\]" -and $_ -match "prepare_vote_commit" }).Count -ge 1) "payment prepare_vote_commit log missing for $orderId."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[PAYMENT\]" -and $_ -match "commit_applied" }).Count -ge 1) "payment commit_applied log missing for $orderId."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[DB-" -and $_ -match "prepare_vote_commit" }).Count -ge 1) "DB prepare_vote_commit log missing for $orderId."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[DB-" -and $_ -match "commit_applied" -and $_ -match "backups_acked=" }).Count -ge 1) "DB commit_applied log (with backups_acked) missing for $orderId."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[EXEC-" -and $_ -match "2pc_decision" -and $_ -match "decision=COMMIT" -and $_ -match "participants=\[db,payment\]" }).Count -ge 1) "executor 2pc_decision=COMMIT (with participants) missing for $orderId."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[EXEC-" -and $_ -match "2pc_commit_applied" }).Count -ge 1) "executor 2pc_commit_applied missing for $orderId."

    # Allow a brief moment for replication-apply logs to settle before the
    # direct-read convergence check.
    Start-Sleep -Seconds 2
    $after = Read-StockOnAllReplicas -Title $title
    foreach ($rid in 1..3) {
        $b = $before[$rid]; $a = $after[$rid]
        Assert-Condition (($b - $a) -eq 1) "DB-$rid stock change was ($b -> $a), expected -1."
    }

    $passed = $failures.Count -eq 0
    $details = if ($passed) {
        "orderId=$orderId before=[$($before[1]),$($before[2]),$($before[3])] after=[$($after[1]),$($after[2]),$($after[3])]"
    }
    else { ($failures -join " ") }

    Add-CheckResult -Name "2pc:valid-commit" -Passed $passed -Details $details
    $script:CurrentFailureList = $null
    return [pscustomobject]@{ Passed = $passed; OrderId = $orderId; After = $after }
}

function Test-OversoldAbort {
    $failures = [System.Collections.Generic.List[string]]::new()
    $script:CurrentFailureList = $failures

    $title = "Book A"
    $before = Read-StockOnAllReplicas -Title $title
    $response = Invoke-Checkout -FilePath "test_checkout_oversold.json"
    $orderId = ([string]$response.Json.orderId).Trim()
    $logs = Wait-For2pcOutcome -OrderId $orderId -ExpectedDecision "ABORT"
    $orderLines = Get-OrderLogLines -Logs $logs -OrderId $orderId

    Assert-Condition ($response.StatusCode -eq 200) "Expected HTTP 200 but got $($response.StatusCode)."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[DB-" -and $_ -match "prepare_vote_abort" }).Count -ge 1) "DB prepare_vote_abort log missing for oversold $orderId."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[EXEC-" -and $_ -match "2pc_decision" -and $_ -match "decision=ABORT" }).Count -ge 1) "executor 2pc_decision=ABORT missing for oversold $orderId."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[EXEC-" -and $_ -match "2pc_abort_applied" }).Count -ge 1) "executor 2pc_abort_applied missing for oversold $orderId."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[PAYMENT\]" -and ($_ -match "abort_ok" -or $_ -match "abort_without_prepare" -or $_ -match "abort_idempotent") }).Count -ge 1) "payment abort log missing for oversold $orderId."
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[DB-" -and $_ -match "commit_applied" }).Count -eq 0) "DB commit_applied unexpectedly fired for oversold $orderId."

    Start-Sleep -Seconds 1
    $after = Read-StockOnAllReplicas -Title $title
    foreach ($rid in 1..3) {
        Assert-Condition ($before[$rid] -eq $after[$rid]) "DB-$rid stock moved on abort: $($before[$rid]) -> $($after[$rid])."
    }

    $passed = $failures.Count -eq 0
    $details = if ($passed) {
        "orderId=$orderId before=[$($before[1]),$($before[2]),$($before[3])] after=[$($after[1]),$($after[2]),$($after[3])]"
    }
    else { ($failures -join " ") }

    Add-CheckResult -Name "2pc:oversold-abort" -Passed $passed -Details $details
    $script:CurrentFailureList = $null
}

function Test-ReplicaConvergence {
    param([string]$Title = "Book A")

    $failures = [System.Collections.Generic.List[string]]::new()
    $script:CurrentFailureList = $failures
    $stock = Read-StockOnAllReplicas -Title $Title
    $values = @($stock[1], $stock[2], $stock[3])
    $distinct = @($values | Sort-Object -Unique)
    Assert-Condition ($distinct.Count -eq 1) "Replicas disagree on '$Title': DB-1=$($stock[1]) DB-2=$($stock[2]) DB-3=$($stock[3])"

    $passed = $failures.Count -eq 0
    $details = if ($passed) { "all three replicas returned $($values[0]) for '$Title'." } else { ($failures -join " ") }
    Add-CheckResult -Name "convergence:read-all-replicas" -Passed $passed -Details $details
    $script:CurrentFailureList = $null
}

function Test-DbPrimaryFailover {
    $failures = [System.Collections.Generic.List[string]]::new()
    $script:CurrentFailureList = $failures

    $primaryId = Wait-ForDbPrimary -TimeoutSeconds 20
    $primaryService = "books_database_$primaryId"
    Write-Host "Stopping current DB primary $primaryService to test failover..."

    $title = "Book A"
    $newPrimary = -1
    try {
        $stopResult = Run-Compose @("stop", $primaryService)
        if ($stopResult.ExitCode -ne 0) { throw "Failed to stop $primaryService.`n$($stopResult.Output)" }

        Start-Sleep -Seconds 8
        # Verify a new primary is elected from the surviving replicas.
        $out = & python scripts/_cp3_db_probe.py find-primary 2>&1
        Assert-Condition ($LASTEXITCODE -eq 0) "no DB primary after failover: $out"
        $m = [regex]::Match(($out | Out-String), "primary_id=(\d+)")
        $newPrimary = if ($m.Success) { [int]$m.Groups[1].Value } else { -1 }
        Assert-Condition ($newPrimary -ne $primaryId -and $newPrimary -ne -1) "Expected a different DB primary, got id=$newPrimary."
    }
    finally {
        Write-Host "Restoring $primaryService..."
        $restoreResult = Run-Compose @("up", "-d", $primaryService)
        if ($restoreResult.ExitCode -ne 0) {
            Add-CheckResult -Name "db-failover:restore" -Passed $false -Details $restoreResult.Output
        }
        else {
            Start-Sleep -Seconds 10
        }
    }

    # After the former primary is restored it reclaims the role via the
    # Bully tie-breaker (higher replica id wins). Phase 14 added
    # kv_store.json persistence (write-then-rename in STATE_DIR), so a
    # restarted replica now loads committed stock from disk instead of
    # reverting to SEED_STOCK. We still drive one checkout and verify
    # all three replicas converge on the same value — an end-to-end
    # sanity check that the re-elected primary can still serve 2PC
    # after a failover+restore cycle.
    Start-Sleep -Seconds 6
    $response = Invoke-Checkout -FilePath "test_checkout.json"
    $orderId = ([string]$response.Json.orderId).Trim()
    $logs = Wait-For2pcOutcome -OrderId $orderId -ExpectedDecision "COMMIT" -TimeoutSeconds 40
    $orderLines = Get-OrderLogLines -Logs $logs -OrderId $orderId
    Assert-Condition (@($orderLines | Where-Object { $_ -match "\[EXEC-" -and $_ -match "2pc_commit_applied" }).Count -ge 1) "2pc_commit_applied missing post-restore for $orderId."

    Start-Sleep -Seconds 3
    $post = Read-StockOnAllReplicas -Title $title
    $postValues = @($post[1], $post[2], $post[3])
    $postDistinct = @($postValues | Sort-Object -Unique)
    Assert-Condition ($postDistinct.Count -eq 1) "Replicas diverged after failover/restore/commit: DB-1=$($post[1]) DB-2=$($post[2]) DB-3=$($post[3])."

    $passed = $failures.Count -eq 0
    $details = if ($passed) { "DB primary $primaryId stopped, replica $newPrimary elected new primary, writes resumed after replica restore." } else { ($failures -join " ") }
    Add-CheckResult -Name "db-failover" -Passed $passed -Details $details
    $script:CurrentFailureList = $null
}

function Test-ParticipantFailureBonus {
    # Runs the existing standalone Python test which arms FAIL_NEXT_COMMIT=2
    # on DB-3, submits a checkout, and asserts the coordinator retries until
    # the injected failures exhaust and the commit lands. Exit code is
    # authoritative.
    $failures = [System.Collections.Generic.List[string]]::new()
    $script:CurrentFailureList = $failures
    $out = & python "order_executor/tests/test_2pc_fail_injection.py" 2>&1
    $ec = $LASTEXITCODE
    Assert-Condition ($ec -eq 0) "fail-injection test exit=$ec output=$($out | Out-String)"
    $passed = $failures.Count -eq 0
    $details = if ($passed) { "coordinator retry absorbed 2 injected commit failures; commit landed; all 3 replicas converged." } else { ($failures -join " ") }
    Add-CheckResult -Name "bonus:participant-failure-recovery" -Passed $passed -Details $details
    $script:CurrentFailureList = $null
}

Write-Section "Environment"

$dockerVersion = & docker --version
Add-CheckResult -Name "docker" -Passed ($LASTEXITCODE -eq 0) -Details $dockerVersion

$composeVersion = & docker compose version
Add-CheckResult -Name "docker-compose" -Passed ($LASTEXITCODE -eq 0) -Details $composeVersion

$configResult = Run-Compose @("config")
Add-CheckResult -Name "compose-config" -Passed ($configResult.ExitCode -eq 0) -Details "docker compose config exited with code $($configResult.ExitCode)."

Write-Section "Startup"

# Always tear down volumes first so every run starts from pristine seed state.
# Prior run's stock mutations on disk would otherwise leak into the next run's
# before/after assertions.
$downResult = Run-Compose @("down", "-v")
Add-CheckResult -Name "compose-down" -Passed ($downResult.ExitCode -eq 0) -Details "Cleared previous stack and volumes."

if ($SkipBuild) {
    $upResult = Run-Compose @("up", "-d")
    Add-CheckResult -Name "compose-up" -Passed ($upResult.ExitCode -eq 0) -Details "Started stack without rebuild."
}
else {
    $upResult = Run-Compose @("up", "--build", "-d")
    Add-CheckResult -Name "compose-up" -Passed ($upResult.ExitCode -eq 0) -Details "Started stack with rebuild."
}

Wait-ForOrchestrator
Add-CheckResult -Name "orchestrator-ready" -Passed $true -Details "HTTP endpoint is reachable."

$reachableOut = & python scripts/_cp3_db_probe.py all-reachable 2>&1
Add-CheckResult -Name "db-all-reachable" -Passed ($LASTEXITCODE -eq 0) -Details ($reachableOut | Out-String).Trim()

$primaryId = Wait-ForDbPrimary -TimeoutSeconds 40
Add-CheckResult -Name "db-primary-elected" -Passed $true -Details "DB primary is books_database_$primaryId."

$psResult = Run-Compose @("ps")
Add-CheckResult -Name "compose-ps" -Passed ($psResult.ExitCode -eq 0) -Details "docker compose ps completed."

Write-Section "Syntax"

foreach ($path in $pythonFiles) {
    python -m py_compile $path
    Add-CheckResult -Name "py-compile:$path" -Passed ($LASTEXITCODE -eq 0) -Details "Syntax OK."
}

Write-Section "2PC: happy path"

Test-ValidCommit | Out-Null

Write-Section "2PC: oversold -> abort"

Test-OversoldAbort

Write-Section "Convergence"

Test-ReplicaConvergence -Title "Book A"

if (-not $SkipFailover) {
    Write-Section "DB primary failover"
    Test-DbPrimaryFailover
}

if (-not $SkipBonus) {
    Write-Section "Bonus: participant-failure recovery"
    Test-ParticipantFailureBonus
}

Write-Section "Summary"

$passedCount = @($results | Where-Object { $_.Passed }).Count
$failedCount = @($results | Where-Object { -not $_.Passed }).Count

foreach ($result in $results) {
    $status = if ($result.Passed) { "PASS" } else { "FAIL" }
    Write-Host ("{0} {1}" -f $status, $result.Name)
}

Write-Host ""
Write-Host ("Passed: {0}" -f $passedCount)
Write-Host ("Failed: {0}" -f $failedCount)

if ($failedCount -gt 0) {
    exit 1
}

exit 0

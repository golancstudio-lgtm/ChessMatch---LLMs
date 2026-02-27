<#
.SYNOPSIS
    Deploys ChessMatch LLM to AWS: CloudFormation, Lambdas, frontend to S3, optional state sync and CloudFront invalidation.

.DESCRIPTION
    Run from the project root (parent of deploy\). Uses AWS CLI (must be installed and configured).
    - Creates or updates the CloudFormation stack (unless -SkipStack).
    - Packages and deploys Lambda code from deploy\lambda\api_state and deploy\lambda\api_events.
    - Syncs frontend\ to the S3 bucket so CloudFront serves the latest UI.
    - Optionally syncs .chess_match_state.json to S3 (-SyncState) and invalidates CloudFront (-InvalidateCloudFront).

.PARAMETER StackName
    CloudFormation stack name (e.g. stack-chess-llms-battle).

.PARAMETER ProjectName
    Must match CloudFormation parameter; used for Lambda naming (default: chessmatch).

.PARAMETER EnvironmentName
    Environment: dev, staging, or prod (default: dev).

.PARAMETER Region
    AWS region (e.g. us-east-1). Default: from AWS_DEFAULT_REGION or us-east-1.

.PARAMETER SkipStack
    If set, skip CloudFormation; still update Lambdas and sync frontend using existing stack outputs.

.PARAMETER SyncState
    If set, upload .chess_match_state.json to S3 as game_state/state.json.

.PARAMETER InvalidateCloudFront
    If set, create a CloudFront invalidation for /* so the new frontend is served immediately.

.PARAMETER Template
    CloudFormation template file name (default: cloudformation.yaml). Use cloudformation-no-cloudfront.yaml for S3 website hosting without CloudFront.

.PARAMETER FrontendBucket
    Override: S3 bucket name for frontend (e.g. chessmatchv2-dev-frontend-556000333328). If not set, read from stack output FrontendBucketName.

.PARAMETER CloudFrontDistributionId
    Override: CloudFront distribution ID (e.g. E3EJ2EQZMME8WL). If not set, read from stack output CloudFrontDistributionId.

.PARAMETER ApiEndpoint
    Override: API Gateway invoke URL (e.g. https://xxx.execute-api.region.amazonaws.com/dev). If not set, read from stack output ApiEndpoint.

.EXAMPLE
    .\deploy\Deploy-Aws.ps1 -StackName stack-chess-llms-battle
.EXAMPLE
    .\deploy\Deploy-Aws.ps1 -StackName stack-chess-llms-battle -SkipStack
.EXAMPLE
    .\deploy\Deploy-Aws.ps1 -StackName stack-chess-llms-battle -SyncState -InvalidateCloudFront
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $StackName,

    [string] $ProjectName = "chessmatch",
    [ValidateSet("dev", "staging", "prod")]
    [string] $EnvironmentName = "dev",
    [string] $Region = $env:AWS_DEFAULT_REGION,
    [switch] $SkipStack,
    [switch] $SyncState,
    [switch] $InvalidateCloudFront,
    [string] $Template = "cloudformation.yaml",
    [string] $FrontendBucket = "",
    [string] $CloudFrontDistributionId = "",
    [string] $ApiEndpoint = ""
)

$ErrorActionPreference = "Stop"

# Resolve paths: script is deploy\Deploy-Aws.ps1, project root is parent of deploy
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$TemplatePath = Join-Path $ScriptDir $Template
$LambdaBase = Join-Path $ScriptDir "lambda"

if (-not $Region) { $Region = "us-east-1" }

$lambdaSuffix = ""
if ($Template -eq "cloudformation-no-cloudfront.yaml") {
    $lambdaSuffix = "-nocf"
}
$StateLambdaName = "${ProjectName}-${EnvironmentName}${lambdaSuffix}-api-state"
$TickLambdaName = "${ProjectName}-${EnvironmentName}${lambdaSuffix}-api-tick"
$EventsLambdaName = "${ProjectName}-${EnvironmentName}${lambdaSuffix}-api-events"
$ResetLambdaName = "${ProjectName}-${EnvironmentName}${lambdaSuffix}-api-reset"
$AdaptersLambdaName = "${ProjectName}-${EnvironmentName}${lambdaSuffix}-api-adapters"
$GameStartLambdaName = "${ProjectName}-${EnvironmentName}${lambdaSuffix}-api-game-start"
$GameRunLambdaName = "${ProjectName}-${EnvironmentName}${lambdaSuffix}-game-run"

# --- CloudFormation deploy ---
if (-not $SkipStack) {
    if (-not (Test-Path -LiteralPath $TemplatePath)) {
        Write-Error "Template not found: $TemplatePath"
    }
    Write-Host "Deploying CloudFormation stack: $StackName (region: $Region)" -ForegroundColor Cyan
    & aws cloudformation deploy `
        --template-file $TemplatePath `
        --stack-name $StackName `
        --parameter-overrides "ProjectName=$ProjectName" "EnvironmentName=$EnvironmentName" `
        --region $Region `
        --capabilities CAPABILITY_NAMED_IAM
    if ($LASTEXITCODE -ne 0) {
        Write-Error "CloudFormation deploy failed."
    }
    Write-Host "Stack deploy completed." -ForegroundColor Green
    # No-CloudFront: force API Gateway stage to use a new deployment (avoids 403 MissingAuthenticationToken for new routes)
    if ($Template -eq "cloudformation-no-cloudfront.yaml") {
        $apiId = (aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs[?OutputKey=='ApiGatewayId'].OutputValue" --output text 2>$null)
        if ($apiId) {
            Write-Host "Creating new API Gateway deployment so stage includes all routes..." -ForegroundColor Cyan
            $deployId = (aws apigateway create-deployment --rest-api-id $apiId --region $Region --query "id" --output text 2>$null)
            if ($deployId) {
                & aws apigateway update-stage --rest-api-id $apiId --stage-name $EnvironmentName --patch-operations "op=replace,path=/deploymentId,value=$deployId" --region $Region 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Host "  API stage updated." -ForegroundColor Green } else { Write-Warning "Update stage failed." }
            }
        }
    }
} else {
    Write-Host "Skipping CloudFormation (SkipStack). Updating Lambda code only." -ForegroundColor Yellow
}

# --- Package and deploy each Lambda ---
function Deploy-Lambda {
    param([string]$Name, [string]$SourceDir)

    $dir = Join-Path $LambdaBase $SourceDir
    if (-not (Test-Path -LiteralPath $dir)) {
        Write-Error "Lambda source dir not found: $dir"
    }

    $zipPath = Join-Path $env:TEMP "${Name}.zip"
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }

    Push-Location $dir
    try {
        Compress-Archive -Path "*.py" -DestinationPath $zipPath -Force
    } finally {
        Pop-Location
    }

    if (-not (Test-Path -LiteralPath $zipPath)) {
        Write-Error "Failed to create zip for $Name"
    }

    Write-Host "Updating Lambda: $Name" -ForegroundColor Cyan
    $prevErrPref = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & aws lambda update-function-code `
            --function-name $Name `
            --zip-file "fileb://$zipPath" `
            --region $Region 2>&1 | Out-Null
    } catch { }
    $ErrorActionPreference = $prevErrPref
    if ($LASTEXITCODE -ne 0) {
        $ErrorActionPreference = "SilentlyContinue"
        try { & aws lambda get-function --function-name $Name --region $Region 2>&1 | Out-Null } catch { }
        $ErrorActionPreference = $prevErrPref
        $getFailed = ($LASTEXITCODE -ne 0)
        if ($getFailed) {
            Write-Warning "  Lambda $Name does not exist; skipping (create it via CloudFormation if needed)."
        } else {
            Write-Error "Lambda update failed for $Name"
        }
    } else {
        Write-Host "  OK $Name" -ForegroundColor Green
    }
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
}

function Deploy-GameRunLambda {
    param([string]$Name)
    $gameRunDir = Join-Path $LambdaBase "game_run"
    $srcDir = Join-Path $ProjectRoot "src"
    if (-not (Test-Path -LiteralPath $gameRunDir)) { Write-Error "game_run dir not found: $gameRunDir" }
    if (-not (Test-Path -LiteralPath $srcDir)) { Write-Error "src dir not found: $srcDir" }
    $buildDir = Join-Path $env:TEMP "chessmatch-game-run-build"
    if (Test-Path -LiteralPath $buildDir) { Remove-Item -Recurse -Force $buildDir }
    New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
    Copy-Item -Path (Join-Path $gameRunDir "handler.py") -Destination (Join-Path $buildDir "handler.py") -Force
    $buildSrc = Join-Path $buildDir "src"
    New-Item -ItemType Directory -Path $buildSrc -Force | Out-Null
    Copy-Item -Path (Join-Path $srcDir "*") -Destination $buildSrc -Recurse -Force
    $reqPath = Join-Path $gameRunDir "requirements.txt"
    if (Test-Path -LiteralPath $reqPath) {
        Write-Host "Installing game_run dependencies (Linux x86_64 for Lambda) into $buildDir..." -ForegroundColor Cyan
        # Lambda runs on Amazon Linux 2. Building on Windows would install Windows wheels (e.g. pydantic_core) that fail at runtime.
        # pip requires --only-binary=:all: when using --platform/--python-version (no source builds).
        & pip install -r $reqPath -t $buildDir --platform manylinux2014_x86_64 --python-version 3.12 --implementation cp --only-binary=:all: --quiet --disable-pip-version-check 2>&1
        if ($LASTEXITCODE -ne 0) { Write-Warning "pip install had errors; continuing." }
    }
    $zipPath = Join-Path $env:TEMP "${Name}.zip"
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    Push-Location $buildDir
    try {
        Compress-Archive -Path * -DestinationPath $zipPath -Force
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $zipPath)) { Write-Error "Failed to create game_run zip" }
    Write-Host "Updating Lambda: $Name (game_run)" -ForegroundColor Cyan
    $prevErrPref = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try { & aws lambda update-function-code --function-name $Name --zip-file "fileb://$zipPath" --region $Region 2>&1 | Out-Null } catch { }
    $ErrorActionPreference = $prevErrPref
    if ($LASTEXITCODE -ne 0) {
        $ErrorActionPreference = "SilentlyContinue"
        try { & aws lambda get-function --function-name $Name --region $Region 2>&1 | Out-Null } catch { }
        $ErrorActionPreference = $prevErrPref
        if ($LASTEXITCODE -ne 0) { Write-Warning "  Lambda $Name does not exist; skipping." }
        else { Write-Error "Lambda update failed for $Name" }
    } else { Write-Host "  OK $Name" -ForegroundColor Green }
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $buildDir -ErrorAction SilentlyContinue
    Write-Host "  OK $Name" -ForegroundColor Green
}

Deploy-Lambda -Name $StateLambdaName -SourceDir "api_state"
Deploy-Lambda -Name $TickLambdaName -SourceDir "api_tick"
Deploy-Lambda -Name $EventsLambdaName -SourceDir "api_events"
Deploy-Lambda -Name $ResetLambdaName -SourceDir "api_reset"
Deploy-Lambda -Name $AdaptersLambdaName -SourceDir "api_adapters"
Deploy-Lambda -Name $GameStartLambdaName -SourceDir "api_game_start"
Deploy-GameRunLambda -Name $GameRunLambdaName

# --- Get stack outputs (for frontend bucket and optional CloudFront); allow overrides ---
$bucket = (aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text 2>$null)
$cfId = (aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" --output text 2>$null)
$endpoint = (aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text 2>$null)
if ($FrontendBucket.Trim()) { $bucket = $FrontendBucket.Trim() }
if ($CloudFrontDistributionId.Trim()) { $cfId = $CloudFrontDistributionId.Trim() }
if ($ApiEndpoint.Trim()) { $endpoint = $ApiEndpoint.Trim() }

# --- Sync frontend to S3 ---
if ($bucket) {
    $frontendDir = Join-Path $ProjectRoot "frontend"
    if (Test-Path -LiteralPath $frontendDir) {
        Write-Host "Syncing frontend to S3: $bucket" -ForegroundColor Cyan
        & aws s3 sync $frontendDir "s3://$bucket/" --exclude "README.md" --region $Region
        if ($LASTEXITCODE -ne 0) { Write-Warning "Frontend sync had errors." } else { Write-Host "  Frontend sync OK." -ForegroundColor Green }
        # No-CloudFront: S3 has no path rewrite, so copy review.html to key "review" so /review serves the review page (not index.html via ErrorDocument)
        if ($Template -eq "cloudformation-no-cloudfront.yaml") {
            $reviewHtml = Join-Path $frontendDir "review.html"
            if (Test-Path -LiteralPath $reviewHtml) {
                & aws s3 cp $reviewHtml "s3://$bucket/review" --content-type "text/html" --region $Region
                if ($LASTEXITCODE -eq 0) { Write-Host "  /review -> review page OK." -ForegroundColor Green } else { Write-Warning "Copy review -> /review failed." }
            }
            # Inject API endpoint into config.js so the frontend can call API Gateway (different origin)
            if ($endpoint) {
                $configContent = "window.CHESSMATCH_API_BASE = '" + $endpoint.Replace("'", "\'") + "';"
                $configPath = Join-Path $env:TEMP "chessmatch-config.js"
                Set-Content -Path $configPath -Value $configContent -Encoding UTF8 -NoNewline
                & aws s3 cp $configPath "s3://$bucket/static/config.js" --content-type "application/javascript" --region $Region
                Remove-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue
                if ($LASTEXITCODE -eq 0) { Write-Host "  config.js (API_BASE) OK." -ForegroundColor Green } else { Write-Warning "Upload config.js failed." }
            }
        }
    } else {
        Write-Warning "Frontend directory not found: $frontendDir"
    }
} else {
    Write-Warning "Could not get FrontendBucketName from stack; skipping frontend sync."
}

# --- Optional: upload game state to S3 ---
if ($SyncState -and $bucket) {
    $stateFile = Join-Path $ProjectRoot ".chess_match_state.json"
    if (Test-Path -LiteralPath $stateFile) {
        Write-Host "Uploading game state to S3 (game_state/state.json)" -ForegroundColor Cyan
        & aws s3 cp $stateFile "s3://$bucket/game_state/state.json" --content-type "application/json" --region $Region
        if ($LASTEXITCODE -eq 0) { Write-Host "  State upload OK." -ForegroundColor Green } else { Write-Warning "State upload failed." }
    } else {
        Write-Warning ".chess_match_state.json not found; skipping state upload."
    }
}

# --- Optional: CloudFront invalidation ---
if ($InvalidateCloudFront -and $cfId) {
    Write-Host "Creating CloudFront invalidation for /*" -ForegroundColor Cyan
    & aws cloudfront create-invalidation --distribution-id $cfId --paths "/*" 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "  Invalidation created." -ForegroundColor Green } else { Write-Warning "CloudFront invalidation failed (check distribution id)." }
}

Write-Host ""
Write-Host "Deployment complete." -ForegroundColor Green
if ($endpoint) { Write-Host "  API endpoint: $endpoint" -ForegroundColor Cyan }
if ($bucket)  { Write-Host "  Frontend bucket: $bucket" -ForegroundColor Cyan }
if ($cfId)    { Write-Host "  CloudFront distribution: $cfId" -ForegroundColor Cyan }

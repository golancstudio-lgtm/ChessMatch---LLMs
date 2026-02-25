<#
.SYNOPSIS
    Deploys ChessMatch LLM to AWS: runs CloudFormation (S3, API Gateway, Lambdas) and uploads Lambda code.

.DESCRIPTION
    Run from the project root (parent of deploy\). Uses AWS CLI (must be installed and configured).
    - Creates or updates the CloudFormation stack.
    - Packages and deploys Lambda code from deploy\lambda\api_state and deploy\lambda\api_events.

.PARAMETER StackName
    CloudFormation stack name (e.g. chessmatch-dev).

.PARAMETER ProjectName
    Must match CloudFormation parameter; used for Lambda naming (default: chessmatch).

.PARAMETER EnvironmentName
    Environment: dev, staging, or prod (default: dev).

.PARAMETER Region
    AWS region (e.g. us-east-1). Default: from AWS_DEFAULT_REGION or us-east-1.

.PARAMETER SkipStack
    If set, only update Lambda code; do not create/update the CloudFormation stack.

.EXAMPLE
    .\deploy\Deploy-Aws.ps1 -StackName chessmatch-dev
.EXAMPLE
    .\deploy\Deploy-Aws.ps1 -StackName chessmatch-dev -SkipStack
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $StackName,

    [string] $ProjectName = "chessmatch",
    [ValidateSet("dev", "staging", "prod")]
    [string] $EnvironmentName = "dev",
    [string] $Region = $env:AWS_DEFAULT_REGION,
    [switch] $SkipStack
)

$ErrorActionPreference = "Stop"

# Resolve paths: script is deploy\Deploy-Aws.ps1, project root is parent of deploy
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$TemplatePath = Join-Path $ScriptDir "cloudformation.yaml"
$LambdaBase = Join-Path $ScriptDir "lambda"

if (-not $Region) { $Region = "us-east-1" }

$StateLambdaName = "${ProjectName}-${EnvironmentName}-api-state"
$EventsLambdaName = "${ProjectName}-${EnvironmentName}-api-events"

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
    & aws lambda update-function-code `
        --function-name $Name `
        --zip-file "fileb://$zipPath" `
        --region $Region
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Lambda update failed for $Name"
    }
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    Write-Host "  OK $Name" -ForegroundColor Green
}

Deploy-Lambda -Name $StateLambdaName -SourceDir "api_state"
Deploy-Lambda -Name $EventsLambdaName -SourceDir "api_events"

Write-Host ""
Write-Host "Deployment complete." -ForegroundColor Green
if (-not $SkipStack) {
    $endpoint = (aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text 2>$null)
    $bucket = (aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text 2>$null)
    if ($endpoint) { Write-Host "  API endpoint: $endpoint" -ForegroundColor Cyan }
    if ($bucket)  { Write-Host "  Frontend S3 bucket: $bucket" -ForegroundColor Cyan }
}

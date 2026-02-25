# AWS deployment (CloudFormation + Lambdas)

- **CloudFormation** (`cloudformation.yaml`): S3 bucket, API Gateway REST API, and two Lambdas for `GET /api/state` and `GET /api/events`. CloudFront is not included; add it manually (e.g. free tier) in front of S3 and/or API as needed.
- **Lambda code** lives under `deploy/lambda/`:
  - `api_state`: reads game state from S3 key `game_state/state.json` and returns JSON (same shape as the local FastAPI `/api/state`).
  - `api_events`: returns a single SSE event (REST API does not support long-lived streams; frontend can poll `/api/state`).
- **Deploy script** (`Deploy-Aws.ps1`): runs the stack and uploads Lambda code from the repo.

## Prerequisites

- AWS CLI installed and configured (`aws configure`).
- Run PowerShell from the **project root** (parent of `deploy/`).

## Deploy stack and Lambdas

```powershell
# From project root
.\deploy\Deploy-Aws.ps1 -StackName chessmatch-dev
```

Optional: `-ProjectName chessmatch`, `-EnvironmentName dev|staging|prod`, `-Region us-east-1`, `-SkipStack` (only update Lambda code, do not run CloudFormation).

## After deploy

- **API base URL**: CloudFormation output `ApiEndpoint`, e.g. `https://xxxx.execute-api.us-east-1.amazonaws.com/dev`. Use this as the frontend `API_BASE` (or set your CloudFront origin to this for `/api/*`).
- **S3 bucket**: Output `FrontendBucketName`. Upload `frontend/` contents (e.g. `index.html`, `review.html`, `static/`) for your static site. For the state Lambda to return live game data, upload the state file to **`game_state/state.json`** (same format as `.chess_match_state.json`). You can sync `.chess_match_state.json` to `s3://<bucket>/game_state/state.json` when running games.

## Lambda-only updates

After changing code in `deploy/lambda/api_state` or `deploy/lambda/api_events`:

```powershell
.\deploy\Deploy-Aws.ps1 -StackName chessmatch-dev -SkipStack
```

This only updates the Lambda function code; it does not run CloudFormation.

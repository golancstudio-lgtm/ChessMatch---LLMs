# AWS deployment (CloudFormation + Lambdas)

- **CloudFormation** (`cloudformation.yaml`): S3 bucket, CloudFront, API Gateway REST API, and Lambdas for the full API: `GET /api/state`, `GET /api/adapters`, `POST /api/game/start`, `POST /api/game/reset`, `GET /api/events`. Frontend is served via CloudFront; `/api/*` is proxied to API Gateway. Each game has its own state in S3 (`game_state/{game_id}.json`).
- **No-CloudFront template** (`cloudformation-no-cloudfront.yaml`): Same API and Lambdas, but frontend is served via **S3 static website hosting** only (no CloudFront). The Review page is at `/review.html` (no rewrite).
- **Lambda code** under `deploy/lambda/`:
  - `api_state`: reads game state from S3 (`game_state/{game_id}.json` from query `game_id`, or `game_state/state.json`).
  - `api_adapters`: returns available LLM adapters for the start-game UI.
  - `api_game_start`: writes initial state to S3, invokes `game_run` async, returns `game_id`.
  - `game_run`: runs the full game loop, writes state to S3 after each move.
  - `api_reset`: clears state for a game (body `game_id` or default key).
  - `api_events`: returns a single SSE event (frontend uses polling when deployed).
- **Deploy script** (`Deploy-Aws.ps1`): runs the stack and uploads all Lambda code from the repo.

## Prerequisites

- AWS CLI installed and configured (`aws configure`).
- Run PowerShell from the **project root** (parent of `deploy/`).

If deploy fails with `AWS::EarlyValidation::ResourceExistenceCheck`, your account may already have resources with the same names (e.g. from an earlier stack). Use a unique **ProjectName** so resource names do not conflict:  
`.\deploy\Deploy-Aws.ps1 -StackName my-stack -ProjectName mychess`

## Deploy stack and Lambdas

```powershell
# From project root
.\deploy\Deploy-Aws.ps1 -StackName chessmatch-dev
```

Optional: `-ProjectName chessmatch`, `-EnvironmentName dev|staging|prod`, `-Region us-east-1`, `-SkipStack` (only update Lambda code, do not run CloudFormation), `-Template cloudformation-no-cloudfront.yaml` (deploy without CloudFront).

## After deploy

- **API base URL**: CloudFormation output `ApiEndpoint`, e.g. `https://xxxx.execute-api.us-east-1.amazonaws.com/dev`. Use this as the frontend `API_BASE` (or set your CloudFront origin to this for `/api/*`).
- **S3 bucket**: Output `FrontendBucketName`. Upload `frontend/` contents (e.g. `index.html`, `review.html`, `static/`) for your static site. For the state Lambda to return live game data, upload the state file to **`game_state/state.json`** (same format as `.chess_match_state.json`). You can sync `.chess_match_state.json` to `s3://<bucket>/game_state/state.json` when running games.

## Update everything in AWS (after code or frontend changes)

From the project root, run the deploy script with your stack name. It will:

- Update the CloudFormation stack (unless you use `-SkipStack`)
- Deploy the latest Lambda code (api_state, api_events)
- **Sync the `frontend/` folder to the S3 bucket** so CloudFront serves the latest UI

```powershell
.\deploy\Deploy-Aws.ps1 -StackName stack-chess-llms-battle
```

Optional flags:

- **`-SkipStack`** – Skip CloudFormation; only update Lambdas and sync frontend (faster when you didn’t change the template).
- **`-SyncState`** – Upload `.chess_match_state.json` to S3 as `game_state/state.json` (so the API returns this state).
- **`-InvalidateCloudFront`** – Create a CloudFront invalidation for `/*` so the new frontend is served immediately (otherwise cache may serve old assets for a while).

Examples:

```powershell
# Full update (stack + Lambdas + frontend)
.\deploy\Deploy-Aws.ps1 -StackName stack-chess-llms-battle

# Code/frontend only (no stack change), then invalidate cache
.\deploy\Deploy-Aws.ps1 -StackName stack-chess-llms-battle -SkipStack -InvalidateCloudFront

# Also push current game state to S3
.\deploy\Deploy-Aws.ps1 -StackName stack-chess-llms-battle -SyncState
```

## Deploy without CloudFront (S3 website only)

To use the no-CloudFront template (S3 static website + API Gateway + Lambdas):

```powershell
.\deploy\Deploy-Aws.ps1 -StackName chessmatch-no-cf -Template cloudformation-no-cloudfront.yaml
```

- **Frontend URL**: Use the stack output `FrontendWebsiteURL` (e.g. `http://bucket.s3-website-region.amazonaws.com`). This is **HTTP only** (no HTTPS) and the bucket is publicly readable.
- **API**: Use output `ApiEndpoint` as the API base. Because the frontend and API are on different origins, set `API_BASE` in `frontend/static/app.js` to the API Gateway URL before syncing, or the UI will call the S3 origin for `/api/*` and get 404.
- **Review page**: The deploy script copies `review.html` to S3 key `review` so `/review` works. Open the site’s `/review` or `/review.html`.
- **Start game**: POST `/api/game/start` writes initial state to S3 and invokes the **game_run** Lambda asynchronously. The game_run Lambda runs the full game loop and writes state to S3 after each move; the UI polls GET `/api/state` for progress. **LLM API keys** are read from **AWS Secrets Manager**: create a secret named `llm-api-secrets` (JSON key-value). Expected keys: `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`, `COHERE_API_KEY`, `GROQ_API_KEY`, `XAI_API_KEY` (only the keys for the adapters you use are required). The Lambda execution role has `secretsmanager:GetSecretValue` on `llm-api-secrets`.

**Verify that the app can talk to OpenAI and Gemini** (locally or using the secret):
- **Local (.env):** From project root run `python scripts/verify_llm_keys.py`. This loads `.env` and makes a minimal call to OpenAI and Gemini; fix any missing or invalid keys.
- **AWS secret:** Run `python scripts/verify_llm_keys.py --aws` (with AWS CLI configured). This loads keys from `llm-api-secrets` and tests them. If this fails, fix the secret (key names and values) and IAM permission for the Lambda role.

## Lambda-only updates

After changing only code in `deploy/lambda/api_state` or `deploy/lambda/api_events`, you can run with `-SkipStack`; the script still syncs the frontend and updates both Lambdas.

## Debugging: game stuck on "Waiting for first move"

If POST `/api/game/start` returns **202** but the UI never shows a move:

1. **Check CloudWatch Logs for the game_run Lambda**  
   In AWS Console: **CloudWatch** → **Log groups** → `/aws/lambda/chessmatch-dev-nocf-game-run` (or your stack’s game-run name). Open the latest log stream for the time you clicked Start. Look for:
   - **"game_run invoked"** – Lambda was triggered.
   - **"Secrets loaded from Secrets Manager"** – Secret `llm-api-secrets` was read; if this is missing, check secret name, IAM permission (`secretsmanager:GetSecretValue` on `llm-api-secrets`), and region.
   - **"Adapters resolved: X vs Y"** – Adapters were found.
   - **"Starting run_game (first move will call LLM)..."** – Game loop started.
   - Any **exception** after that (e.g. OpenAI/Gemini API error, timeout, missing API key) will show the failure reason.

2. **Check that the start Lambda invoked game_run**  
   In **CloudWatch** → `/aws/lambda/chessmatch-dev-nocf-api-game-start`: confirm there are no errors when it writes to S3 and calls `lambda.invoke` for the game_run function.

3. **Check S3 state**  
   In the frontend bucket, open `game_state/state.json`. After Start you should see initial state (`white_name`, `black_name`, `move_history: []`). If the game_run Lambda runs and applies the first move, this file will update with the first move and `move_log`.

4. **Redeploy with logging**  
   The game_run handler logs the steps above. Redeploy the Lambdas (`.\deploy\Deploy-Aws.ps1 ... -Template cloudformation-no-cloudfront.yaml`), start a game again, then check the game_run log stream.

5. **Invoke game_run synchronously to see the exact error**  
   With **synchronous** invoke you get the Lambda response (or error) directly instead of relying on async + CloudWatch. From your machine (AWS CLI configured, same region as the Lambdas):

   ```powershell
   # Replace with your game_run function name and region
   $fn = "chessmatch-dev-nocf-game-run"
   $region = "us-east-1"
   aws lambda invoke --function-name $fn --payload '{"white_llm_id":"chatgpt","black_llm_id":"gemini","max_retries":3}' --cli-binary-format raw-in-base64-out --region $region deploy\lambda\game_run\response.json
   type deploy\lambda\game_run\response.json
   ```

   Or use the test event file:

   ```powershell
   aws lambda invoke --function-name chessmatch-dev-nocf-game-run --payload fileb://deploy/lambda/game_run/test_event.json --cli-binary-format raw-in-base64-out --region us-east-1 deploy\lambda\game_run\response.json
   type deploy\lambda\game_run\response.json
   ```

   If the Lambda fails, `response.json` will contain the error payload (e.g. `{"statusCode":500,"body":"{\"error\":\"...\",\"detail\":\"...\"}"}`). The first move can take 30–60 seconds; the CLI will wait. This confirms whether the failure is in secrets, adapters, or the first LLM call.

6. **Confirm secret and Lambda in the same region**  
   The `llm-api-secrets` secret must be in the **same AWS region** as the game_run Lambda. If the Lambda is in `us-east-1`, create or copy the secret in `us-east-1`.

## AWS prod: Review and Restart buttons

- **Review link** (`/review`): On AWS, the frontend is served from S3; there is no object at key `review`, only `review.html`. The CloudFormation template includes a CloudFront Function that rewrites requests for `/review` to `/review.html` so the review page loads. If the Review link shows the main page instead, ensure the stack is deployed with the `ReviewRewriteFunction` and the default cache behavior has the function association; then sync frontend and invalidate CloudFront.
- **Restart button** (POST `/api/game/reset`): The reset Lambda and API Gateway route must be deployed; the frontend sends `Accept: application/json`. If the button "does nothing", open the browser Network tab and check whether the POST returns 200 with JSON or a CORS/4xx error. The UI now also treats a 200 response without JSON (e.g. proxy issues) by refreshing state once.

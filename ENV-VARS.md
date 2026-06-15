# Fund Registry Environment Variables

This is the checked-in reference for environment variables read by `app.py`.

| Name | Default | Controls | Production posture |
|---|---|---|---|
| `FUND_REGISTRY_CORS_ORIGINS` | empty | Optional comma-separated CORS allowlist. | Leave empty unless a named browser origin needs API access. |
| `FUND_REGISTRY_ALLOWED_HOSTS` | `fundregistry.org,www.fundregistry.org,localhost,127.0.0.1,testserver` | Trusted host allowlist. Empty/unset falls back to the default list. | Keep set to the production hostnames if overriding; do not use a wildcard. |
| `FUND_REGISTRY_STATIC_DIR` | `static` | Static asset root. | Use a deployed static directory. |
| `BITCOIN_SSH_HOST` | unset | SSH host used when `BITCOIN_BACKEND=ssh`. | Set only when intentionally using a remote Bitcoin Core node. |
| `BITCOIN_BACKEND` | `local` | Bitcoin Core backend mode: `local`, `ssh`, `remote`, `bitcoind-local`, or `bitcoind-ssh`. | Production should match your intended Bitcoin Core transport; do not silently fail over. |
| `FUND_REGISTRY_DB_PATH` | `data/fundregistry.db` | SQLite database path. | Use persistent data storage. |
| `FUND_REGISTRY_PHOTO_DIR` | `data/story-photos` | Story photo storage path. | Use persistent data storage. |
| `FUND_REGISTRY_TRANSACTION_CACHE_DIR` | `data/tx-cache` | Transaction cache directory. | Use persistent cache storage. |
| `FUND_REGISTRY_MESSAGES_PATH` | `data/messages.jsonl` | Contact-message JSONL store. | Use persistent data storage; protect admin token separately. |
| `FUND_REGISTRY_PUBLIC_BASE_URL` | `https://fundregistry.org` | Canonical public base URL. | Keep at the active public domain. |
| `FUND_REGISTRY_MEMPOOL_BASE_URL` | `https://mempool.space/api` | Mempool API base for address/transaction lookups. | Use the approved public upstream unless a failover is deliberate. |
| `FUND_REGISTRY_TRANSACTION_CACHE_TTL_SECONDS` | `600` | Transaction cache TTL. | Keep bounded so payment/proof checks do not stale too long. |
| `FUND_REGISTRY_ALLOW_DEV_ACTIONS` | `false` | Enables `/v1/dev/payments/{payment_id}/mark-paid`. | Must remain `false` in production. |
| `FUND_REGISTRY_PAYMENT_MODE` | `disabled` | Payment mode: `disabled`, `mock`, or `bitcoin-core`. | Use `bitcoin-core` only after local preflight and live checkout testing; `disabled` is safe. |
| `FUND_REGISTRY_PROOF_MODE` | `disabled` | Proof mode: `disabled`, `mock`, `bitcoin-message`, or `mixed`. | Use `mixed` only when both mock/dev and Bitcoin-message flows are intentional. |
| `FUND_REGISTRY_ANCHOR_MODE` | `disabled` | Anchor mode: `disabled`, `mock`, or `bitcoin-core`. | Use `bitcoin-core` only after anchor preflight and a live smoke test. |
| `FUND_REGISTRY_SATS_PER_USD` | `1200` | Amount conversion fallback. | Keep aligned with your deployment policy. |
| `FUND_REGISTRY_TIER2_AMOUNT_SATS` | unset | Optional Tier 2 sat amount override. | Set deliberately for live checkout tests only. |
| `FUND_REGISTRY_TIER3_AMOUNT_SATS` | unset | Optional Tier 3 sat amount override. | Set deliberately for live checkout tests only. |
| `FUND_REGISTRY_REQUEST_TIMEOUT_SECONDS` | `10.0` | Outbound request timeout. | Keep finite; avoid long public request hangs. |
| `FUND_REGISTRY_PAYMENTS_PAUSED` | `false` | Pauses payment initiation while keeping the app up. | Use for temporary checkout holds; clear only after live verification. |
| `FUND_REGISTRY_PAYMENT_UI_REDACTED` | `false` | Hides payment details during gated testing. | Keep in sync with staged rollout state. |
| `FUND_REGISTRY_MESSAGES_ADMIN_TOKEN` | unset | Admin token for contact-message read-state mutations. | Inject via secret/drop-in only; never commit a value. |
| `BITCOIN_CLI_PATH` | `bitcoin-cli` | Bitcoin CLI binary path. | Point at the Bitcoin Core CLI binary used by this deployment. |
| `BITCOIN_CONF_PATH` | empty | Optional Bitcoin Core config path. | Set only if your `bitcoin-cli` requires `-conf=...`. |
| `BITCOIN_WALLET_NAME` | `fund-registry-anchor` | Wallet used for proof/anchor backend checks. | Must name the synced wallet used by this deployment. |
| `FUND_REGISTRY_PAYMENT_WALLET_NAME` | `fund-registry-payments` | Wallet used for payment receive addresses. | Must name the dedicated payment wallet. |
| `FUND_REGISTRY_PAYMENT_CONFIRMATION_TARGET` | `1` | Required confirmations before payment success. | Keep at the approved checkout target. |
| `FUND_REGISTRY_PAYMENT_EXPIRY_MINUTES` | `15` | Payment request expiry window. | Keep short enough to avoid stale checkout UX. |
| `FUND_REGISTRY_DISABLE_AUTO_APP` | `false` | Prevents module import from constructing the FastAPI app. | Test/import helper only; do not set for the service. |
| `FUND_REGISTRY_HOST` | `127.0.0.1` | Uvicorn bind host when running `app.py` directly. | Keep loopback behind nginx. |
| `FUND_REGISTRY_PORT` | `43134` | Uvicorn bind port when running `app.py` directly. | Keep aligned with nginx/systemd. |

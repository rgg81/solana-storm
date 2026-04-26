#!/usr/bin/env bash
# Idempotent GitHub setup for solana-storm: 12 labels, 6 milestones,
# 1 Projects v2 board, 29 issues. Re-running is safe — every helper checks
# for prior state.
set -euo pipefail

REPO="rgg81/solana-storm"
OWNER="rgg81"

# Pre-flight: gh auth must include the `project` scope (Projects v2 GraphQL).
if ! gh auth status 2>&1 | grep -qE "Token scopes:.*'(project|read:project)"; then
  echo "ERROR: gh token is missing the 'project' scope (required for Projects v2)." >&2
  echo "Run: gh auth refresh -h github.com -s project,read:project" >&2
  echo "Then re-run this script." >&2
  exit 1
fi

# ---------- helpers ----------

ensure_label() {
  local name="$1" color="$2" desc="$3"
  gh label create "$name" --repo "$REPO" --color "$color" --description "$desc" --force >/dev/null
  echo "  label: $name"
}

ensure_milestone() {
  local title="$1" desc="$2"
  if gh api "repos/$REPO/milestones" --jq '.[].title' | grep -Fxq "$title"; then
    echo "  milestone exists: $title"
  else
    gh api "repos/$REPO/milestones" -f title="$title" -f description="$desc" >/dev/null
    echo "  milestone: $title"
  fi
}

ensure_issue() {
  local title="$1" milestone="$2" labels="$3" body="$4"
  if gh issue list --repo "$REPO" --state all --limit 200 --json title --jq '.[].title' | grep -Fxq "$title"; then
    echo "  issue exists: $title"
    return
  fi
  gh issue create --repo "$REPO" --title "$title" --milestone "$milestone" --label "$labels" --body "$body" >/dev/null
  echo "  issue: $title"
}

# ---------- labels ----------

echo "Creating labels..."
ensure_label "phase-0" "c5def5" "Setup & infrastructure"
ensure_label "phase-1" "0075ca" "Rust + Solana foundations (Weeks 1-4)"
ensure_label "phase-2" "008672" "Real-time feeds & CEX (Weeks 5-8)"
ensure_label "phase-3" "0e8a16" "Opportunity detection (Weeks 9-12)"
ensure_label "phase-4" "fbca04" "Tx building & Jito (Weeks 13-16)"
ensure_label "phase-5" "8957e5" "Predictive ML layer (Weeks 17-22)"
ensure_label "rust-learning" "d4691f" "Introduces new Rust concepts"
ensure_label "solana" "9945ff" "Touches Solana on-chain code"
ensure_label "cex" "f5a623" "Touches CEX/Binance code"
ensure_label "ml" "e11d48" "ML / ONNX / Python work"
ensure_label "infra" "586069" "Infrastructure / deployment / external accounts"
ensure_label "setup" "bfd4f2" "One-time bootstrap task"

# ---------- milestones ----------

echo "Creating milestones..."
ensure_milestone "Phase 0: Setup & Infrastructure" "Repo bootstrap + external accounts + servers"
ensure_milestone "Phase 1: Rust + Solana Foundations" "Source plan Phase 1 (Weeks 1-4)"
ensure_milestone "Phase 2: Real-time Feeds & CEX" "Source plan Phase 2 (Weeks 5-8)"
ensure_milestone "Phase 3: Opportunity Detection" "Source plan Phase 3 (Weeks 9-12)"
ensure_milestone "Phase 4: Tx Building & Jito" "Source plan Phase 4 (Weeks 13-16)"
ensure_milestone "Phase 5: Predictive ML Layer" "Source plan Phase 5 (Weeks 17-22)"

# ---------- projects v2 board ----------

echo "Creating Projects v2 board..."
PROJECT_NUMBER=$(gh project list --owner "$OWNER" --format json --jq '.projects[] | select(.title=="Solana Storm Roadmap") | .number' || true)
if [[ -z "$PROJECT_NUMBER" ]]; then
  PROJECT_NUMBER=$(gh project create --owner "$OWNER" --title "Solana Storm Roadmap" --format json --jq '.number')
  echo "  created project #$PROJECT_NUMBER"
  gh project link "$PROJECT_NUMBER" --owner "$OWNER" --repo "$REPO" >/dev/null || true
else
  echo "  project exists: #$PROJECT_NUMBER"
fi
echo "PROJECT_NUMBER=$PROJECT_NUMBER" > /tmp/solana-storm-project-number

# ---------- Phase 0: Setup & Infrastructure (7 issues) ----------

echo "Creating Phase 0 issues..."

ensure_issue \
  "Bootstrap repo: README, .gitignore, LICENSE, .env.example" \
  "Phase 0: Setup & Infrastructure" \
  "phase-0,setup" \
  "$(cat <<'EOF'
**Phase:** 0 — Setup

## What
Initial repo skeleton so `main` is non-empty and downstream issues have a place to land code.

## Tasks
- [x] `README.md` — name, one-paragraph project description, link to GitHub Projects board, link to milestones
- [x] `.gitignore` — `target/`, `.env`, `*.swp`, `.DS_Store`
- [x] `LICENSE` — MIT
- [x] `.env.example` — placeholders for Helius, Binance, Telegram, Postgres, keypair
- [x] `scripts/setup-github-issues.sh` — committed alongside

## Acceptance
- `git log --oneline` shows the bootstrap commit on `main`
- `gh issue list --repo rgg81/solana-storm` returns 29 issues

## Note
This work was completed by the issue-creation execution and is being closed on creation.
EOF
)"

ensure_issue \
  "GitHub Actions CI: cargo fmt + clippy + check + test" \
  "Phase 0: Setup & Infrastructure" \
  "phase-0,setup" \
  "$(cat <<'EOF'
**Phase:** 0 — Setup

## What
Bare-bones CI that runs on every push and PR to `main`. Will start producing useful signal once Week 1 lands the Cargo workspace.

## Tasks
- [ ] `.github/workflows/ci.yml` triggered on `push` (main) and `pull_request`
- [ ] Job uses `actions/checkout@v4` and `dtolnay/rust-toolchain@stable` with `components: rustfmt,clippy`
- [ ] Cache `~/.cargo/registry`, `~/.cargo/git`, and `target/` via `actions/cache@v4` keyed on `Cargo.lock`
- [ ] Step: `cargo fmt --all -- --check`
- [ ] Step: `cargo clippy --workspace --all-targets -- -D warnings`
- [ ] Step: `cargo check --workspace --all-targets`
- [ ] Step: `cargo test --workspace`
- [ ] Confirm a green run on `main` (workflow may pass trivially before Week 1 since there is no code yet)

## Acceptance
- Workflow file exists at `.github/workflows/ci.yml`
- A push to `main` triggers the workflow and it goes green
EOF
)"

ensure_issue \
  "Provision Hetzner CX22 VPS (Falkenstein) + base config" \
  "Phase 0: Setup & Infrastructure" \
  "phase-0,infra" \
  "$(cat <<'EOF'
**Phase:** 0 — Setup

## What
Production-ish VPS for the bot to run on. Hetzner CX22 (~€4/mo) in Falkenstein (FSN1).

## Tasks
- [ ] Create Hetzner Cloud account at https://www.hetzner.com/cloud (if not already)
- [ ] Provision `CX22` (2 vCPU / 4 GB / 40 GB SSD) in `fsn1` (Falkenstein), Ubuntu 24.04
- [ ] Add your SSH key (paste `~/.ssh/id_rsa.pub`) during creation
- [ ] First boot: `apt update && apt upgrade -y`
- [ ] Install `ufw` and `fail2ban`; `ufw default deny incoming`, `ufw allow 22`, `ufw enable`
- [ ] Create non-root user `storm` with `sudo` group; copy `~/.ssh/authorized_keys`
- [ ] Disable root SSH login and password auth in `/etc/ssh/sshd_config` (`PermitRootLogin no`, `PasswordAuthentication no`); `systemctl restart ssh`
- [ ] Add VPS to `~/.ssh/config` locally as `Host storm` for easy access

## Acceptance
- `ssh storm` connects key-only without password prompts
- `sudo ufw status` shows port 22 open and nothing else
- `systemctl status fail2ban` shows it running
EOF
)"

ensure_issue \
  "Sign up for Helius Developer plan + obtain RPC endpoint" \
  "Phase 0: Setup & Infrastructure" \
  "phase-0,infra,solana" \
  "$(cat <<'EOF'
**Phase:** 0 — Setup

## What
Helius Developer plan ($49/mo) for Solana RPC + WebSocket + basic Yellowstone gRPC.

## Tasks
- [ ] Sign up at https://helius.dev
- [ ] Subscribe to Developer plan ($49/mo)
- [ ] Generate an API key in the dashboard
- [ ] Save the key to local `.env` and to the VPS `~/.env` (do NOT commit)
- [ ] Confirm the RPC URL pattern `https://mainnet.helius-rpc.com/?api-key=<KEY>`
- [ ] Smoke test: `curl -s https://mainnet.helius-rpc.com/?api-key=$HELIUS_API_KEY -X POST -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"getHealth"}'` returns `{"result":"ok",...}`

## Acceptance
- `getHealth` JSON-RPC call returns `"ok"` over the Helius URL
- Key stored in `.env` (gitignored) and on the VPS
EOF
)"

ensure_issue \
  "Create Binance API key (read-only, IP-restricted)" \
  "Phase 0: Setup & Infrastructure" \
  "phase-0,infra,cex" \
  "$(cat <<'EOF'
**Phase:** 0 — Setup

## What
Read-only Binance API key for spot + futures market data. Trading and withdrawal permissions stay disabled — this bot does not place CEX orders programmatically (CEX leg of CEX-DEX arb is manual or out of scope initially).

## Tasks
- [ ] In Binance: Account → API Management → Create API
- [ ] Permissions: enable **Read Info** only; **disable** Spot Trading, Futures Trading, Withdrawals
- [ ] IP whitelist: add the Hetzner VPS IP (issue #3)
- [ ] Save key + secret to local `.env` and VPS `~/.env` (gitignored)
- [ ] Smoke test public endpoint: `curl -s "https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDC"` returns a price
- [ ] Smoke test signed endpoint: HMAC-sign a `GET /api/v3/account` request — response should include balances without permission errors

## Acceptance
- Public ticker call returns a price
- Signed `/api/v3/account` returns account info (no permission errors)
- IP whitelist active
EOF
)"

ensure_issue \
  "Create Telegram bot for notifications (token + chat ID)" \
  "Phase 0: Setup & Infrastructure" \
  "phase-0,infra" \
  "$(cat <<'EOF'
**Phase:** 0 — Setup

## What
Telegram bot to receive bot status, regime alerts, and daily P&L messages.

## Tasks
- [ ] In Telegram, DM `@BotFather` → `/newbot` → choose name + username (e.g. `solana_storm_alerts_bot`)
- [ ] Save the issued bot token to local `.env` as `TELEGRAM_BOT_TOKEN=...`
- [ ] Send `/start` to your new bot from your personal Telegram account
- [ ] Fetch `https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates` and copy the `chat.id` from the latest message
- [ ] Save chat ID to `.env` as `TELEGRAM_CHAT_ID=...`
- [ ] Smoke test: `curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" -d chat_id=$TELEGRAM_CHAT_ID -d text=hello-from-storm` — message arrives in your Telegram

## Acceptance
- Test message lands in your DM with the bot
- Token + chat ID stored in `.env` (gitignored) and on the VPS
EOF
)"

ensure_issue \
  "Provision PostgreSQL on VPS + scripts/setup-db.sh skeleton" \
  "Phase 0: Setup & Infrastructure" \
  "phase-0,infra" \
  "$(cat <<'EOF'
**Phase:** 0 — Setup

## What
Local PostgreSQL on the VPS for storing prices, opportunities, executions, P&L, and ML training datasets.

## Tasks
- [ ] On VPS: `sudo apt install -y postgresql-16` (or current LTS in Ubuntu 24.04 — `postgresql` meta-package is fine)
- [ ] Generate a random password for role `storm` (e.g. `openssl rand -base64 24`)
- [ ] As `postgres` user: `createuser -P storm` (paste the generated password); `createdb -O storm storm`
- [ ] Confirm `pg_hba.conf` allows `local` and `host` `md5` for the `storm` role on database `storm`
- [ ] Add `DATABASE_URL=postgres://storm:<pwd>@localhost:5432/storm` to VPS `~/.env` (gitignored)
- [ ] Add `scripts/setup-db.sh` skeleton: idempotent role + db creation, runs `psql -f migrations/*.sql` once those exist; safe to re-run
- [ ] Smoke test: `psql "$DATABASE_URL" -c '\l'` shows `storm` database

## Acceptance
- `psql "$DATABASE_URL"` connects from the `storm` user account
- `scripts/setup-db.sh` runs cleanly on a second invocation (idempotent)
EOF
)"

# ---------- Phase 1: Rust + Solana Foundations (Weeks 1-4) ----------

echo "Creating Phase 1 issues..."

ensure_issue \
  "Week 1 — Project scaffolding + Solana basics" \
  "Phase 1: Rust + Solana Foundations" \
  "phase-1,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 1 — Rust + Solana Foundations
**Week:** 1 of 22

## Learning goals

**Rust:**
- Workspace organization with `Cargo.toml`
- `Result<T, E>` and the `?` operator
- `thiserror` derive macros
- `serde::Deserialize` for config structs
- Basic async with `#[tokio::main]`

**Solana:**
- Account model: everything is an account (programs, data, tokens)
- Account ownership: each account is owned by a program
- Rent and lamports
- RPC JSON-RPC interface

## Tasks
- [ ] Set up the workspace with `storm-core` and `storm-solana` crates
- [ ] Implement `config.rs` with the `config` crate (load from TOML + env vars)
- [ ] Implement `error.rs` with `thiserror` (define `StormError` enum with variants for RPC, parse, config errors)
- [ ] Write your first Solana RPC call: `getAccountInfo` for a known token mint using `solana-client`
- [ ] Parse the response manually, understanding `AccountInfo { data, owner, lamports, executable }`

## Deliverable (definition of done)
CLI that connects to Solana mainnet and prints the balance of a given address.

## Crates / paths
- Workspace root `Cargo.toml` — `resolver = "2"`, `members = ["crates/*", "bins/*"]`
- `crates/storm-core/{Cargo.toml, src/{lib.rs, config.rs, types.rs, error.rs, math.rs}}`
- `crates/storm-solana/{Cargo.toml, src/{lib.rs, rpc.rs}}`
- `config/default.toml`
EOF
)"

ensure_issue \
  "Week 2 — Token accounts and SPL Token" \
  "Phase 1: Rust + Solana Foundations" \
  "phase-1,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 1 — Rust + Solana Foundations
**Week:** 2 of 22

## Learning goals

**Rust:**
- `borsh` deserialization (Solana's native serialization)
- Struct design with `derive` macros
- `impl Display` for custom formatting
- Unit testing with `#[cfg(test)]`
- `From` / `TryFrom` trait implementations

**Solana:**
- SPL Token program and associated token accounts
- Mint accounts vs token accounts
- PDA (Program Derived Address) concept

## Tasks
- [ ] Deserialize SPL Token accounts (`Mint`, `TokenAccount`) using `spl-token` and `borsh`
- [ ] Fetch all token accounts for a wallet using `getTokenAccountsByOwner`
- [ ] Parse and display: mint, amount, decimals, owner
- [ ] Implement `types.rs` in `storm-core` with domain types: `Token`, `TokenAmount`, `Price`
- [ ] Write unit tests for deserialization

## Deliverable (definition of done)
CLI that shows a complete token portfolio for any Solana wallet.

## Crates / paths
- `crates/storm-core/src/types.rs`
- `crates/storm-solana/src/accounts.rs`
- `bins/storm-cli/` (initial scaffold for inspection commands)
EOF
)"

ensure_issue \
  "Week 3 — DEX pool state parsing (Raydium)" \
  "Phase 1: Rust + Solana Foundations" \
  "phase-1,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 1 — Rust + Solana Foundations
**Week:** 3 of 22

## Learning goals

**Rust:**
- Trait definition and implementation
- Generics and trait bounds
- `Decimal` arithmetic with `rust_decimal`
- Property-based testing (consider the `proptest` crate)

**Solana:**
- AMM mechanics: constant product, reserves, swap math
- Raydium account layout (pool state, vault accounts, oracle)
- Account data deserialization with fixed offsets

## Tasks
- [ ] Fetch a Raydium AMM pool account and deserialize its state
- [ ] Calculate the spot price from pool reserves (constant product `x * y = k`)
- [ ] Implement `pools.rs` with a `DexPool` trait — methods: `token_a()`, `token_b()`, `price()`, `reserves()`, `calculate_swap_output(input)`
- [ ] Implement `DexPool` for Raydium AMM (standard constant-product)
- [ ] Write property-based tests: swap output should always be less than reserves; price stays within fee-adjusted bounds

## Deliverable (definition of done)
CLI that prints the current price of SOL/USDC on Raydium with reserves.

## Crates / paths
- `crates/storm-solana/src/pools.rs` (trait + Raydium impl)
- `crates/storm-core/src/math.rs` (fee-adjusted swap math, slippage)
EOF
)"

ensure_issue \
  "Week 4 — Multi-DEX support (Orca Whirlpools)" \
  "Phase 1: Rust + Solana Foundations" \
  "phase-1,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 1 — Rust + Solana Foundations
**Week:** 4 of 22

## Learning goals

**Rust:**
- Trait objects vs generics (`Box<dyn DexPool>` vs `impl DexPool`)
- `sqlx` async database queries with compile-time checked SQL
- `chrono` for timestamps
- Module organization across crates

**Solana:**
- Concentrated liquidity (CLMM) vs constant-product AMM
- Orca Whirlpool account structure
- Different DEXs have fundamentally different pricing models

## Tasks
- [ ] Implement `DexPool` trait for Orca Whirlpools (concentrated liquidity — different math)
- [ ] Understand the tick-based pricing model vs constant-product
- [ ] Compare prices across Raydium and Orca for the same pair
- [ ] Calculate theoretical arbitrage: if Raydium price ≠ Orca price, what is the opportunity?
- [ ] Store price snapshots in PostgreSQL using `sqlx`
- [ ] Implement `storm-store` crate with basic schema: `prices`, `pools`

## Deliverable (definition of done)
CLI that shows price comparison across Raydium and Orca for top 10 pairs, with theoretical arb size.

## Crates / paths
- `crates/storm-solana/src/pools.rs` (extend with Whirlpool impl)
- `crates/storm-store/{Cargo.toml, src/{lib.rs, db.rs, models.rs, migrations/}}`
- `scripts/setup-db.sh` (extend with prices/pools migration)
EOF
)"

# ---------- Phase 2: Real-time Feeds & CEX (Weeks 5-8) ----------

echo "Creating Phase 2 issues..."

ensure_issue \
  "Week 5 — Binance WebSocket feed" \
  "Phase 2: Real-time Feeds & CEX" \
  "phase-2,rust-learning,cex" \
  "$(cat <<'EOF'
**Phase:** 2 — Real-time Feeds & CEX Integration
**Week:** 5 of 22

## Learning goals

**Rust:**
- `tokio-tungstenite` WebSocket client
- `tokio::sync::broadcast` and `mpsc` channels
- `Stream` trait and `futures-util` combinators
- Graceful shutdown with `tokio::signal`
- Reconnection patterns with `loop` + `tokio::time::sleep`

## Tasks
- [ ] Implement `storm-cex` crate
- [ ] Connect to Binance spot WebSocket (`bookTicker` stream for real-time best bid/ask)
- [ ] Connect to Binance Futures WebSocket (`markPrice` stream for funding rates)
- [ ] Normalize into a unified `PriceTick { symbol, bid, ask, timestamp, source }` type
- [ ] Use `tokio::sync::broadcast` channel to distribute ticks to multiple consumers
- [ ] Handle reconnection logic with exponential backoff

## Deliverable (definition of done)
Daemon that prints real-time Binance prices for SOL, ETH, BTC with auto-reconnect.

## Crates / paths
- `crates/storm-cex/{Cargo.toml, src/{lib.rs, ws.rs, types.rs, feed.rs}}`
- `bins/storm-monitor/` (first iteration of monitoring daemon)
EOF
)"

ensure_issue \
  "Week 6 — Solana on-chain price streaming (WebSocket)" \
  "Phase 2: Real-time Feeds & CEX" \
  "phase-2,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 2 — Real-time Feeds & CEX Integration
**Week:** 6 of 22

## Learning goals

**Rust:**
- Multiple concurrent WebSocket connections with `tokio::select!`
- Shared state with `Arc<RwLock<T>>`
- Consumer pattern: multiple async tasks reading from the same broadcast channel

**Solana:**
- WebSocket subscription model (`accountSubscribe`, `programSubscribe`)
- Commitment levels: `processed` vs `confirmed` vs `finalized`
- Account change notifications and delta updates

## Tasks
- [ ] Subscribe to Raydium/Orca pool account updates via Solana WebSocket (`accountSubscribe`)
- [ ] On each account update, re-deserialize the pool state and recalculate the price
- [ ] Feed into the same `PriceTick` broadcast channel as Binance
- [ ] Implement `storm-engine/cex_dex.rs` (initial): compare CEX and DEX prices in real-time
- [ ] Log detected dislocations with size and duration

## Deliverable (definition of done)
Dashboard-like output showing real-time CEX vs DEX prices for SOL/USDC, highlighting dislocations > 0.1%.

## Crates / paths
- `crates/storm-solana/src/{rpc.rs, pools.rs}` (extend)
- `crates/storm-engine/{Cargo.toml, src/{lib.rs, cex_dex.rs}}`
EOF
)"

ensure_issue \
  "Week 7 — Yellowstone gRPC integration" \
  "Phase 2: Real-time Feeds & CEX" \
  "phase-2,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 2 — Real-time Feeds & CEX Integration
**Week:** 7 of 22

## Learning goals

**Rust:**
- gRPC with `tonic` (Yellowstone uses protobuf over gRPC)
- Protobuf deserialization
- Streaming responses with `tonic::Streaming<T>`
- Benchmarking with `std::time::Instant`

**Solana:**
- Geyser plugin architecture (validator-level data streaming)
- Slot-based data delivery vs RPC polling
- Why gRPC is faster than WebSocket for MEV

## Tasks
- [ ] Connect to Yellowstone gRPC (Helius Developer plan includes basic gRPC access)
- [ ] Subscribe to account updates for specific pool addresses
- [ ] Compare latency: WebSocket vs gRPC for the same accounts (log timestamps both ways)
- [ ] Implement `geyser.rs` in `storm-solana`
- [ ] Build a filtered subscription: only DEX pool accounts for configured pairs

## Deliverable (definition of done)
Latency comparison report: WebSocket vs gRPC for price feed freshness.

## Crates / paths
- `crates/storm-solana/src/geyser.rs`
- `crates/storm-solana/Cargo.toml` (add `yellowstone-grpc-client`, `yellowstone-grpc-proto`, `tonic`)
EOF
)"

ensure_issue \
  "Week 8 — Stress detector + market regime classification" \
  "Phase 2: Real-time Feeds & CEX" \
  "phase-2,rust-learning,cex" \
  "$(cat <<'EOF'
**Phase:** 2 — Real-time Feeds & CEX Integration
**Week:** 8 of 22

## Learning goals

**Rust:**
- State machine pattern with enums
- `tokio::sync::watch` for state broadcasting
- Builder pattern for detector configuration
- `tracing` structured logging with spans

## Tasks
- [ ] Implement `detector.rs` in `storm-cex`:
  - [ ] Funding rate spike detector (perp funding beyond ±0.1%)
  - [ ] Open interest drop detector (rapid OI decline = liquidation cascade starting)
  - [ ] Volume spike detector (abnormal trade volume on Binance)
  - [ ] Volatility regime classifier: `Normal`, `Elevated`, `Stress`, `Extreme`
- [ ] Implement `state.rs` in `storm-engine` — `BotState` enum: `Dormant`, `Alert`, `Active`, `Executing`, `Cooldown`
- [ ] Integrate Telegram notifications (`storm-notify`) for state transitions
- [ ] Log all detected stress events to PostgreSQL

## Deliverable (definition of done)
Bot that monitors Binance 24/7, detects market stress, sends Telegram alerts, and transitions between operational states.

## Crates / paths
- `crates/storm-cex/src/detector.rs`
- `crates/storm-engine/src/state.rs`
- `crates/storm-notify/{Cargo.toml, src/{lib.rs, telegram.rs}}`
- `crates/storm-store/src/models.rs` (add `stress_events` table)
EOF
)"

# ---------- Phase 3: Opportunity Detection (Weeks 9-12) ----------

echo "Creating Phase 3 issues..."

ensure_issue \
  "Week 9 — On-chain arbitrage detection" \
  "Phase 3: Opportunity Detection" \
  "phase-3,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 3 — Opportunity Detection
**Week:** 9 of 22

## Learning goals

**Rust:**
- Iterator combinators (`.filter_map()`, `.max_by_key()`, `.collect()`)
- `Ord` trait implementation for opportunity ranking
- Concurrent scanning with `tokio::JoinSet`

**Solana:**
- Swap fee structures across DEXs
- Slippage as a function of pool depth
- Route optimization (direct vs multi-hop)

## Tasks
- [ ] Implement `arb.rs` in `storm-engine`:
  - [ ] For each configured pair, compare prices across all connected DEXs
  - [ ] Calculate net profit after: swap fees, slippage estimate, priority fee, Jito tip
  - [ ] Score opportunities by expected net profit
- [ ] Implement slippage estimation based on pool depth and trade size
- [ ] Build a configurable pair scanner (expand from SOL/USDC to top 20 pairs)
- [ ] Log all detected opportunities (executed or not) to PostgreSQL

## Deliverable (definition of done)
Opportunity scanner logging theoretical arb profits across DEX pairs every 10 seconds.

## Crates / paths
- `crates/storm-engine/src/arb.rs`
- `crates/storm-store/src/models.rs` (add `opportunities` table)
- `config/default.toml` (extend with pair list + thresholds)
EOF
)"

ensure_issue \
  "Week 10 — CEX-DEX arbitrage detection" \
  "Phase 3: Opportunity Detection" \
  "phase-3,rust-learning,cex,solana" \
  "$(cat <<'EOF'
**Phase:** 3 — Opportunity Detection
**Week:** 10 of 22

## Learning goals

**Rust:**
- Cross-crate data flow (CEX feed → engine → executor)
- Time-series analysis in Rust
- Statistical functions (mean, stddev, percentiles) with `rust_decimal`

## Tasks
- [ ] Implement `cex_dex.rs` fully:
  - [ ] Compare Binance spot mid-price against Raydium/Orca pool price
  - [ ] When dislocation exceeds threshold (configurable, e.g. > 30 bps): determine direction (buy on-chain or sell on-chain), calculate expected profit after on-chain swap fees + slippage + Jito tip
  - [ ] Handle the timing aspect: signal is CEX price moving; execution is on-chain
- [ ] Build a backlog analysis: using historical price data, how often did dislocations > 30 bps occur? How long did they persist?

## Deliverable (definition of done)
Real-time CEX-DEX dislocation monitor with historical analysis report.

## Crates / paths
- `crates/storm-engine/src/cex_dex.rs` (extend from Week 6)
- `crates/storm-store/src/models.rs` (add `dislocations` table)
- `bins/storm-cli/src/main.rs` (subcommand for the historical analysis report)
EOF
)"

ensure_issue \
  "Week 11 — Lending protocol monitoring (Kamino/Marginfi)" \
  "Phase 3: Opportunity Detection" \
  "phase-3,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 3 — Opportunity Detection
**Week:** 11 of 22

## Learning goals

**Rust:**
- Complex nested struct deserialization
- Working with `Pubkey` and PDA derivation
- Cross-program account relationships (obligation → reserve → oracle)

**Solana:**
- Lending protocol architecture (obligations, reserves, oracles)
- Pyth oracle account structure and price feeds
- Health factor calculation and liquidation mechanics
- How oracle price updates cascade into liquidation opportunities

## Tasks
- [ ] Implement `lending.rs` in `storm-solana`:
  - [ ] Deserialize Kamino lending obligation accounts
  - [ ] Calculate health factor: `collateral_value / borrow_value`
  - [ ] Monitor Pyth oracle accounts for price feed updates
  - [ ] When health factor drops below liquidation threshold, flag as opportunity
- [ ] Implement `liquidation.rs` in `storm-engine`:
  - [ ] Scan obligation accounts for HF approaching liquidation
  - [ ] Calculate liquidation bonus (protocol-specific, typically 5-10%)
  - [ ] Estimate profitability: bonus minus gas minus Jito tip
- [ ] Subscribe to oracle account updates (Pyth) via gRPC to detect price changes that trigger liquidations

## Deliverable (definition of done)
Dashboard showing top 50 closest-to-liquidation positions on Kamino, with estimated bonus.

## Crates / paths
- `crates/storm-solana/src/{lending.rs, oracle.rs}`
- `crates/storm-engine/src/liquidation.rs`
EOF
)"

ensure_issue \
  "Week 12 — Unified opportunity pipeline" \
  "Phase 3: Opportunity Detection" \
  "phase-3,rust-learning" \
  "$(cat <<'EOF'
**Phase:** 3 — Opportunity Detection
**Week:** 12 of 22

## Learning goals

**Rust:**
- Enum-based polymorphism (Rust's alternative to inheritance)
- `BinaryHeap` for priority queue
- Prometheus metrics with the `metrics` crate
- Integration testing across crates

## Tasks
- [ ] Implement `opportunity.rs` in `storm-engine`:
  - [ ] `OpportunityType` enum: `OnChainArb { from_dex, to_dex }`, `CexDexArb { direction }`, `Liquidation { protocol, obligation }`
  - [ ] `Opportunity { id, opportunity_type, expected_profit_usd, confidence, urgency, detected_at, market_regime }`
- [ ] Merge all three detection streams into a priority queue
- [ ] Filter by minimum profit threshold (configurable)
- [ ] Filter by current market regime (only execute during `Stress`/`Extreme`, or always — configurable)
- [ ] Prometheus metrics: opportunities detected, by type, by regime
- [ ] PostgreSQL logging: full opportunity history for backtesting

## Deliverable (definition of done)
Unified monitor displaying all detected opportunities ranked by expected profit, with Prometheus metrics.

## Crates / paths
- `crates/storm-engine/src/opportunity.rs`
- `crates/storm-notify/src/metrics.rs` (Prometheus exporter)
- `crates/storm-store/src/models.rs` (extend `opportunities`)
EOF
)"

# ---------- Phase 4: Tx Building & Jito (Weeks 13-16) ----------

echo "Creating Phase 4 issues..."

ensure_issue \
  "Week 13 — Solana transaction anatomy" \
  "Phase 4: Tx Building & Jito" \
  "phase-4,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 4 — Tx Building & Jito Integration
**Week:** 13 of 22

## Learning goals

**Rust:**
- Working with raw bytes (`Vec<u8>`, `[u8; 32]`)
- `Keypair` and `Signer` traits
- Serialization to wire format
- HTTP client with `reqwest` for Jupiter API

**Solana:**
- Transaction structure: header, account keys, recent blockhash, instructions
- Instruction anatomy: program_id, accounts (with `is_signer`, `is_writable`), data
- Compute budget and compute units
- `simulateTransaction` RPC method
- Recent blockhash lifecycle and expiry

## Tasks
- [ ] Implement `builder.rs` in `storm-executor`:
  - [ ] Build a simple SOL transfer transaction from scratch (no SDK shortcuts)
  - [ ] Understand `Message`, `Instruction`, `AccountMeta`, `Signature`
  - [ ] Build a swap instruction for Raydium AMM (manually construct the instruction data)
  - [ ] Build a swap instruction via Jupiter API (HTTP quote → swap instruction)
- [ ] Implement `simulate.rs`: call `simulateTransaction` and parse the result
  - [ ] Extract: compute units consumed, logs, error (if any), return data
  - [ ] Verify swap output matches expectation before submission

## Deliverable (definition of done)
CLI that builds, simulates, and displays a Raydium swap transaction (without submitting).

## Crates / paths
- `crates/storm-executor/{Cargo.toml, src/{lib.rs, builder.rs, simulate.rs}}`
- `bins/storm-cli/src/main.rs` (`build-tx` subcommand)
EOF
)"

ensure_issue \
  "Week 14 — Jito bundle integration" \
  "Phase 4: Tx Building & Jito" \
  "phase-4,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 4 — Tx Building & Jito Integration
**Week:** 14 of 22

## Learning goals

**Rust:**
- gRPC client with `tonic` (Jito block engine)
- Protobuf message construction
- Atomic operations concept (all-or-nothing bundles)
- Testing with devnet (conditional compilation `#[cfg(feature = "devnet")]`)

**Solana:**
- Jito bundle mechanics: atomic execution, tip auction
- Block engine endpoints (US East, EU, Tokyo)
- Bundle status lifecycle: `Pending`, `Landed`, `Failed`
- Relationship between tip amount and inclusion probability
- Uncled blocks and their impact on bundles

## Tasks
- [ ] Implement `jito.rs` in `storm-executor`:
  - [ ] Build a Jito bundle (up to 5 transactions)
  - [ ] Add tip instruction (transfer SOL to Jito tip account)
  - [ ] **Important:** tip instruction MUST be in the same transaction as the MEV strategy (not a separate tx)
  - [ ] Submit via Jito Block Engine (JSON-RPC and gRPC)
  - [ ] Parse bundle status response
- [ ] Implement tip calculation: `calculate_tip(expected_profit, tip_pct) -> u64` in lamports, minimum 1000 lamports
- [ ] Implement `simulateBundle` for pre-flight verification
- [ ] Test on devnet with a simple backrun scenario

## Deliverable (definition of done)
Successfully submit a simple Jito bundle on devnet that transfers SOL with a tip.

## Crates / paths
- `crates/storm-executor/src/{jito.rs, simulate.rs}`
- `crates/storm-executor/Cargo.toml` (add `jito-sdk-rust`, `jito-protos`)
EOF
)"

ensure_issue \
  "Week 15 — Strategy-specific execution" \
  "Phase 4: Tx Building & Jito" \
  "phase-4,rust-learning,solana" \
  "$(cat <<'EOF'
**Phase:** 4 — Tx Building & Jito Integration
**Week:** 15 of 22

## Learning goals

**Rust:**
- Complex async orchestration (quote → build → simulate → submit)
- Timeout handling (`tokio::time::timeout`)
- Error recovery and retry patterns
- Feature flags for enabling/disabling strategies

## Tasks
- [ ] Swap execution for on-chain arb:
  1. Fetch Jupiter quote for leg 1 (buy on cheaper DEX)
  2. Fetch Jupiter quote for leg 2 (sell on more expensive DEX)
  3. Build both swap instructions
  4. Package into a Jito bundle with tip
  5. Simulate → verify profit → submit
- [ ] Liquidation execution:
  1. Build liquidation instruction for Kamino/Marginfi
  2. Optionally flash-borrow the repayment token (if available)
  3. Package with tip into Jito bundle
  4. Simulate → verify bonus received → submit
- [ ] CEX-DEX execution:
  1. On CEX signal, build on-chain swap instruction
  2. Package with tip into Jito bundle
  3. Simulate → verify on-chain price still favorable → submit
  4. (CEX leg manual or via Binance API — out of scope for Rust bot initially)

## Deliverable (definition of done)
End-to-end execution pipeline that can detect an opportunity and build + simulate the full transaction bundle.

## Crates / paths
- `crates/storm-executor/src/{builder.rs, jito.rs, submit.rs, nonce.rs}`
- `bins/storm-execute/{Cargo.toml, src/main.rs}` (separate execution daemon)
EOF
)"

ensure_issue \
  "Week 16 — Paper trading, monitoring, and hardening" \
  "Phase 4: Tx Building & Jito" \
  "phase-4,rust-learning,infra" \
  "$(cat <<'EOF'
**Phase:** 4 — Tx Building & Jito Integration
**Week:** 16 of 22

## Learning goals

**Rust:**
- Integration testing with external services
- Graceful shutdown patterns with `tokio::signal` and `CancellationToken`
- Prometheus exposition with `metrics-exporter-prometheus`
- Deployment and `systemd` service configuration

## Tasks
- [ ] Paper trading mode:
  - [ ] Full pipeline runs but skips actual submission
  - [ ] Records: "would have submitted bundle X with expected profit Y"
  - [ ] After some time passes, check if the opportunity was captured by someone else (check on-chain)
  - [ ] Calculate: win rate, average profit, latency to detection
- [ ] Comprehensive Prometheus metrics:
  - [ ] `opportunities_detected_total` (by type, by regime)
  - [ ] `opportunities_profitable_total` (simulated profit > 0)
  - [ ] `bundles_submitted_total` / `bundles_landed_total`
  - [ ] `latency_detection_ms` (event → opportunity detection)
  - [ ] `latency_submission_ms` (detection → bundle submission)
  - [ ] `pnl_realized_usd` (actual P&L)
- [ ] Graceful shutdown: cancel pending orders/bundles, flush metrics + logs, close DB connections
- [ ] Integration tests for the full pipeline (using `solana-test-validator`)
- [ ] Deploy to VPS and run in paper mode for at least 1 week

## Deliverable (definition of done)
Fully operational paper-trading bot deployed on VPS, producing a daily P&L report via Telegram.

## Crates / paths
- `bins/storm-monitor/src/main.rs` (paper-mode flag, graceful shutdown)
- `crates/storm-notify/src/metrics.rs`
- `scripts/deploy.sh` (rsync + systemd service file)
EOF
)"

# ---------- Phase 5: Predictive ML Layer (Weeks 17-22) ----------

echo "Creating Phase 5 issues..."

ensure_issue \
  "Week 17 — Data extraction and dataset construction" \
  "Phase 5: Predictive ML Layer" \
  "phase-5,rust-learning,ml" \
  "$(cat <<'EOF'
**Phase:** 5 — Predictive ML Layer
**Week:** 17 of 22

## Learning goals

**Rust:**
- HTTP client for API pagination (`reqwest` with rate limiting)
- CSV/Parquet reading with `polars` or `arrow` crates
- Batch database inserts with `sqlx`
- Data pipeline design (extract → transform → load)

## Tasks
- [ ] Dune/Flipside SQL: extract all Kamino/Marginfi liquidation events (last 12 months) — wallet, collateral, borrow, HF at liquidation, oracle price at trigger, slot, bonus, liquidator
- [ ] Extract CEX-DEX arbitrage transactions detected by Jito (last 12 months) — pair, DEX, dislocation, profit, tip, time-of-day, congestion
- [ ] Extract on-chain arb transactions — pairs, DEXs, route, profit, gas, tip
- [ ] Download Binance historical data — klines (1m, 5m), funding rates, OI, liquidation stream
- [ ] Download Pyth oracle price history for top 20 Solana tokens
- [ ] Build unified dataset in PostgreSQL — join on-chain events with CEX timestamps (closest block_time ↔ Binance ts)
- [ ] Export training/validation/test splits as Parquet

## External data sources
- Dune Analytics (free 2,500 queries/mo) — `dune.com/api`
- Flipside Crypto (free 100k query-seconds/mo) — `flipsidecrypto.xyz`
- Helius Historical API — included in $49/mo plan
- Birdeye API (free 1,000 req/day) — `public-api.birdeye.so`
- Pyth benchmarks API — `pyth.network`
- Binance Market Data — `data.binance.vision`
- Google BigQuery `bigquery-public-data.crypto_solana_mainnet_us` (free 1TB/mo)

## Deliverable (definition of done)
Clean, merged dataset in PostgreSQL + Parquet with 12 months of liquidation events, arb opportunities, and CEX data, ready for model training.

## Crates / paths
- `crates/storm-store/` (extend with Parquet export)
- `scripts/extract/` (Python or Rust scripts for data pulls; Python is fine here)
EOF
)"

ensure_issue \
  "Week 18 — Liquidation prediction model" \
  "Phase 5: Predictive ML Layer" \
  "phase-5,ml" \
  "$(cat <<'EOF'
**Phase:** 5 — Predictive ML Layer
**Week:** 18 of 22

## Learning goals
None new in Rust this week (Python-heavy). You are preparing the model that Rust will consume in Week 20.

## Tasks
- [ ] **Feature engineering** (this is where your quant skillset dominates):
  - [ ] Per lending position snapshot: HF, HF velocity (Δ over last N blocks), collateral token volatility (from Binance), collateral-borrow correlation, distance to liquidation threshold, borrower's historical behavior (has this wallet been liquidated before?), time since last oracle update, current market regime (from Binance funding/OI)
  - [ ] Market context: overall DEX volume (rolling 1h), Jito tip percentile (how competitive is block space right now?), number of active liquidators (competition proxy)
- [ ] **Model training** (Python, LightGBM):
  - [ ] Target: binary classification — will this position be liquidated in the next N minutes? (experiment with N = 5, 15, 30)
  - [ ] Train LightGBM with the quant researcher / quant engineer iteration workflow from your `crypto-trade` repo
  - [ ] Evaluate: AUC-ROC, precision@recall thresholds, calibration curve
  - [ ] Feature importance analysis → prune low-value features
- [ ] **Export model to ONNX** for Rust inference:
```python
import onnxmltools
from skl2onnx.common.data_types import FloatTensorType
onnx_model = onnxmltools.convert_lightgbm(
    model,
    initial_types=[("features", FloatTensorType([None, n_features]))]
)
onnxmltools.utils.save_model(onnx_model, "liquidation_predictor.onnx")
```

## Deliverable (definition of done)
A trained LightGBM model with >0.85 AUC-ROC on liquidation prediction at T+15min, exported as ONNX.

## Crates / paths
- `models/liquidation_predictor.onnx` (artifact, not committed; track in DVC or LFS)
- `scripts/train/liquidation.py` (training notebook → script)
EOF
)"

ensure_issue \
  "Week 19 — CEX-DEX dislocation model + regime classifier" \
  "Phase 5: Predictive ML Layer" \
  "phase-5,ml" \
  "$(cat <<'EOF'
**Phase:** 5 — Predictive ML Layer
**Week:** 19 of 22

## Learning goals
Same as Week 18 — Python/LightGBM heavy, no new Rust this week.

## Tasks
- [ ] **CEX-DEX dislocation model:**
  - [ ] Target: regression — given a CEX price move of X% in the last 30 seconds, predict the expected on-chain price dislocation magnitude and duration
  - [ ] Features: CEX price change velocity, DEX pool depth, current network congestion (TPS), time of day, day of week, token liquidity tier, recent oracle update frequency
  - [ ] Output: "this CEX move will create a ~45 bps dislocation on Raydium that persists for ~25 seconds" — enough to decide whether to act
  - [ ] Train in Python, export to ONNX
- [ ] **Market regime classifier:**
  - [ ] Target: multi-class — `Calm` / `Trending` / `Volatile` / `Cascading`
  - [ ] Features: Binance funding rate z-score across top 10 symbols, OI change velocity, liquidation flow rate, bid-ask spread widening, cross-asset correlation structure, recent Jito tip distribution (high tips = competitive environment)
  - [ ] Replaces hard-coded thresholds in `detector.rs`
  - [ ] Train in Python, export to ONNX
- [ ] **Optimal tip model:**
  - [ ] Target: regression — predict minimum tip for >90% bundle inclusion probability
  - [ ] Features: current slot congestion, recent tip distribution (p50, p90), number of pending bundles, time within slot, market volatility regime
  - [ ] Train in Python, export to ONNX

## Deliverable (definition of done)
Three ONNX models ready for Rust integration: dislocation predictor, regime classifier, tip optimizer.

## Crates / paths
- `models/{dislocation_predictor.onnx, regime_classifier.onnx, tip_optimizer.onnx}`
- `scripts/train/{dislocation.py, regime.py, tip.py}`
EOF
)"

ensure_issue \
  "Week 20 — ONNX inference in Rust" \
  "Phase 5: Predictive ML Layer" \
  "phase-5,rust-learning,ml" \
  "$(cat <<'EOF'
**Phase:** 5 — Predictive ML Layer
**Week:** 20 of 22

## Learning goals

**Rust:**
- FFI (Foreign Function Interface) with C/C++ libraries via `ort`
- `ndarray` for numerical computing in Rust
- Model lifecycle management (load, warm-up, inference, reload)
- Benchmarking with the `criterion` crate

**Solana:**
- How to integrate ML predictions into the execution decision pipeline

## Tasks
- [ ] Implement `storm-ml` crate; add to workspace
- [ ] Add `ort` (ONNX Runtime bindings, `features = ["load-dynamic"]`) and `ndarray = "0.16"` to dependencies
- [ ] Implement `inference.rs`: load ONNX models, run inference, return predictions
  - [ ] `ModelRegistry` struct with `liquidation_model`, `dislocation_model`, `regime_model`, `tip_model` (each `ort::Session`)
  - [ ] Methods: `predict_liquidation(&FeatureVector) -> Result<f32>`, `predict_dislocation(...)`, `classify_regime(...)`, `predict_optimal_tip(...)`
- [ ] Implement `features.rs`: real-time feature vector computation from live data streams
  - [ ] Inputs from `storm-cex` (Binance ticks) and `storm-solana` (pool states, oracle prices)
  - [ ] Rolling statistics: moving averages, z-scores, velocity, correlation
  - [ ] Outputs `FeatureVector` matching the training schema
- [ ] Benchmark inference latency: target <5ms per prediction (LightGBM via ONNX is typically <1ms)

## Deliverable (definition of done)
`storm-ml` crate running all four models with <5ms inference latency, fed by live feature streams.

## Crates / paths
- `crates/storm-ml/{Cargo.toml, src/{lib.rs, features.rs, liquidation.rs, dislocation.rs, regime.rs, tip.rs, inference.rs}}`
EOF
)"

ensure_issue \
  "Week 21 — Model-driven execution pipeline" \
  "Phase 5: Predictive ML Layer" \
  "phase-5,rust-learning,ml" \
  "$(cat <<'EOF'
**Phase:** 5 — Predictive ML Layer
**Week:** 21 of 22

## Learning goals

**Rust:**
- Complex async orchestration with prediction-driven control flow
- Pre-computation and caching patterns
- State management for "prepared but not yet submitted" transactions

## Tasks
- [ ] Rewire `storm-engine` to use ML predictions instead of simple thresholds:
  - [ ] `liquidation.rs`: replace HF threshold with "liquidation probability > 0.8 → prepare transaction in memory, submit when HF actually crosses 1.0"
  - [ ] `cex_dex.rs`: replace fixed bps threshold with model prediction of dislocation magnitude and duration to decide if the opportunity is worth pursuing at our latency
  - [ ] `state.rs`: replace hard-coded regime thresholds with the ML regime classifier
  - [ ] `jito.rs`: replace fixed 50% tip with model-predicted optimal tip
- [ ] Implement "pre-positioning":
  - [ ] When liquidation model says >80% probability for a position in next 15 minutes:
    1. Pre-compute the full liquidation transaction
    2. Pre-fetch the latest blockhash
    3. Hold the transaction in memory, ready to sign and submit
    4. On oracle update → check HF → if liquidatable → submit immediately
  - [ ] The fast bots start computing *after* the oracle update. You started 15 minutes ago.
- [ ] Add model prediction logging to PostgreSQL for later analysis

## Deliverable (definition of done)
Full pipeline where ML models drive execution decisions: regime classifier activates scanning, liquidation predictor pre-positions transactions, dislocation model filters CEX-DEX opportunities, tip model optimizes bundle economics.

## Crates / paths
- `crates/storm-engine/src/{liquidation.rs, cex_dex.rs, state.rs}` (rewire)
- `crates/storm-executor/src/jito.rs` (use tip model)
- `crates/storm-store/src/models.rs` (add `model_predictions` table)
EOF
)"

ensure_issue \
  "Week 22 — Backtesting, validation, and model monitoring" \
  "Phase 5: Predictive ML Layer" \
  "phase-5,rust-learning,ml" \
  "$(cat <<'EOF'
**Phase:** 5 — Predictive ML Layer
**Week:** 22 of 22

## Learning goals

**Rust:**
- Replay/simulation architecture (feeding historical data through the live pipeline)
- Statistical evaluation metrics in Rust
- Model monitoring and observability patterns

## Tasks
- [ ] Build a backtesting harness:
  - [ ] Replay historical data (from Week 17 datasets) through the full pipeline
  - [ ] For each historical liquidation event: did the model predict it? How far in advance? Would the pre-positioned transaction have landed?
  - [ ] For each historical CEX-DEX dislocation: did the model correctly estimate magnitude and duration? Would the trade have been profitable at our latency?
  - [ ] Calculate: theoretical P&L, win rate, average advance warning, false positive rate
- [ ] Implement model monitoring in production:
  - [ ] Track prediction accuracy over time (calibration drift)
  - [ ] Alert via Telegram when model performance degrades below threshold
  - [ ] Implement periodic model retraining trigger (when accuracy drops >5%)
- [ ] Compare results:
  - [ ] Pipeline WITHOUT ML (Phase 4 thresholds only) vs WITH ML (Phase 5 models)
  - [ ] Quantify: how many additional opportunities does ML capture? How much does tip optimization save?

## Deliverable (definition of done)
Backtesting report comparing threshold-based vs ML-based strategies, with quantified edge from intelligence. Decision point: does the ML layer justify continuing to invest time?

## Crates / paths
- `crates/storm-ml/src/backtest.rs` (replay harness)
- `bins/storm-cli/src/main.rs` (`backtest` subcommand)
- `crates/storm-notify/` (model-drift alerts)
EOF
)"

# ---------- Add all created issues to the Projects board ----------

echo "Adding issues to project board..."
source /tmp/solana-storm-project-number
gh issue list --repo "$REPO" --state all --limit 50 --json number,url --jq '.[].url' | while read -r url; do
  gh project item-add "$PROJECT_NUMBER" --owner "$OWNER" --url "$url" >/dev/null 2>&1 || true
done
echo "  done"

# ---------- Close the bootstrap issue with a reference to the bootstrap commit ----------

echo "Closing the bootstrap issue..."
if BOOTSTRAP_HASH=$(git rev-parse HEAD 2>/dev/null); then
  ISSUE_NUM=$(gh issue list --repo "$REPO" --search "Bootstrap repo in:title" --state open --json number --jq '.[0].number // empty')
  if [[ -n "$ISSUE_NUM" ]]; then
    gh issue comment "$ISSUE_NUM" --repo "$REPO" --body "Done in commit $BOOTSTRAP_HASH." >/dev/null
    gh issue close "$ISSUE_NUM" --repo "$REPO" --reason completed >/dev/null
    echo "  closed issue #$ISSUE_NUM"
  else
    echo "  bootstrap issue already closed (skipping)"
  fi
else
  echo "  no bootstrap commit on HEAD; skipping bootstrap-issue close"
fi

echo
echo "Done. Summary:"
gh issue list --repo "$REPO" --state all --limit 50 --json number,title,milestone --jq 'group_by(.milestone.title)[] | "\(.[0].milestone.title): \(length) issues"'
echo "Project board: https://github.com/users/$OWNER/projects/$PROJECT_NUMBER"

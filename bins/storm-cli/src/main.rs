use std::str::FromStr;

use clap::{Parser, Subcommand};
use solana_sdk::pubkey::Pubkey;
use storm_core::{Config, Token, TokenAmount};
use storm_solana::{DexPool, OrcaWhirlpool, RaydiumPool, RpcContext};
use storm_store::{Dex, PoolRow, PriceSnapshot, Store};

const LAMPORTS_PER_SOL: u64 = 1_000_000_000;

#[derive(Parser)]
#[command(name = "storm-cli", about = "Solana Storm CLI", version)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Fetch and print the balance + AccountInfo for a Solana address.
    Balance {
        /// Public key (base58)
        address: String,
    },
    /// List every SPL token holding for a wallet.
    Portfolio {
        /// Wallet public key (base58)
        wallet: String,
    },
    /// Fetch and print a single SPL Mint (decimals, supply, authorities).
    Mint {
        /// Mint public key (base58)
        mint: String,
    },
    /// Fetch a Raydium AMM v4 pool: reserves, spot price, fee, sample swap.
    Pool {
        /// Raydium AMM v4 pool address (base58)
        address: String,
    },
    /// Fetch an Orca Whirlpool (concentrated liquidity).
    Whirlpool {
        /// Orca Whirlpool address (base58)
        address: String,
    },
    /// Compare a Raydium pool and an Orca Whirlpool for the same pair.
    Compare {
        /// Raydium AMM v4 pool address
        raydium: String,
        /// Orca Whirlpool address
        orca: String,
    },
    /// Local-storage operations (SQLite).
    Db {
        #[command(subcommand)]
        command: DbCommand,
    },
}

#[derive(Subcommand)]
enum DbCommand {
    /// Initialise the SQLite store and run migrations.
    Init {
        /// SQLite URL (default: sqlite://./storm.db)
        #[arg(long, default_value = "sqlite://./storm.db")]
        db: String,
    },
    /// Snapshot the current price of a Raydium pool and an Orca Whirlpool.
    Log {
        /// Raydium AMM v4 pool address
        raydium: String,
        /// Orca Whirlpool address
        orca: String,
        /// SQLite URL (default: sqlite://./storm.db)
        #[arg(long, default_value = "sqlite://./storm.db")]
        db: String,
    },
}

fn known_symbol(mint: &Pubkey) -> Option<&'static str> {
    const SOL: Pubkey = solana_sdk::pubkey!("So11111111111111111111111111111111111111112");
    const USDC: Pubkey = solana_sdk::pubkey!("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v");
    const USDT: Pubkey = solana_sdk::pubkey!("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB");
    if *mint == SOL {
        Some("SOL")
    } else if *mint == USDC {
        Some("USDC")
    } else if *mint == USDT {
        Some("USDT")
    } else {
        None
    }
}

fn label_for(mint: &Pubkey) -> String {
    match known_symbol(mint) {
        Some(s) => format!("{s} ({mint})"),
        None => mint.to_string(),
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Load .env (gitignored) so SOLANA_RPC_URL / Helius credentials are picked up.
    dotenvy::dotenv().ok();

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let cli = Cli::parse();
    let cfg = Config::load()?;
    let rpc = RpcContext::from_config(&cfg.solana);

    match cli.command {
        Command::Balance { address } => {
            let snap = rpc.fetch_account(&address).await?;
            let sol = snap.lamports as f64 / LAMPORTS_PER_SOL as f64;
            println!("address    : {}", snap.address);
            println!("lamports   : {} ({:.9} SOL)", snap.lamports, sol);
            println!("owner      : {}", snap.owner);
            println!("executable : {}", snap.executable);
            println!("data bytes : {}", snap.data_len);
            println!("rent epoch : {}", snap.rent_epoch);
            println!("slot       : {}", snap.slot);
        }
        Command::Portfolio { wallet } => {
            let owner = Pubkey::from_str(&wallet)
                .map_err(|e| anyhow::anyhow!("invalid wallet pubkey '{wallet}': {e}"))?;
            let entries = rpc.fetch_portfolio(&owner).await?;
            if entries.is_empty() {
                println!("(no SPL token accounts found for {owner})");
                return Ok(());
            }
            println!("portfolio for {owner} — {} token account(s)", entries.len());
            println!(
                "{:<44}  {:<44}  {:>24}  {:>10}",
                "TOKEN_ACCOUNT", "MINT", "AMOUNT", "DECIMALS"
            );
            for e in &entries {
                println!(
                    "{:<44}  {:<44}  {:>24}  {:>10}",
                    e.token_account,
                    e.holding.token.mint,
                    e.holding.ui_amount(),
                    e.holding.token.decimals
                );
            }
        }
        Command::Mint { mint } => {
            let pk = Pubkey::from_str(&mint)
                .map_err(|e| anyhow::anyhow!("invalid mint pubkey '{mint}': {e}"))?;
            let info = rpc.fetch_mint(&pk).await?;
            println!("address          : {}", info.address);
            println!("decimals         : {}", info.decimals);
            println!("supply (raw)     : {}", info.supply);
            println!(
                "mint authority   : {}",
                info.mint_authority
                    .map(|p| p.to_string())
                    .unwrap_or_else(|| "<none>".into())
            );
            println!(
                "freeze authority : {}",
                info.freeze_authority
                    .map(|p| p.to_string())
                    .unwrap_or_else(|| "<none>".into())
            );
        }
        Command::Pool { address } => {
            let pk = Pubkey::from_str(&address)
                .map_err(|e| anyhow::anyhow!("invalid pool pubkey '{address}': {e}"))?;
            let pool = rpc.fetch_raydium_pool(&pk).await?;
            let (ra, rb) = pool.reserves();
            let a_sym = known_symbol(&pool.token_a().mint).unwrap_or("A");
            let b_sym = known_symbol(&pool.token_b().mint).unwrap_or("B");
            println!("pool           : {}", pool.pool_address());
            println!("program        : {}", pool.program_id());
            println!(
                "token A        : {}  decimals={}",
                label_for(&pool.token_a().mint),
                pool.token_a().decimals
            );
            println!(
                "token B        : {}  decimals={}",
                label_for(&pool.token_b().mint),
                pool.token_b().decimals
            );
            println!("reserve A      : {} {a_sym}", ra.ui_amount());
            println!("reserve B      : {} {b_sym}", rb.ui_amount());
            println!(
                "swap fee       : {} / {} ({} bps)",
                pool.state.swap_fee_numerator,
                pool.state.swap_fee_denominator,
                pool.state.swap_fee_numerator * 10_000 / pool.state.swap_fee_denominator.max(1)
            );
            println!(
                "spot price     : {} {b_sym} per {a_sym}",
                pool.price().round_dp(6)
            );
            // Sample swap: 1 unit of A → B
            let one_a = TokenAmount::new(
                Token::new(pool.token_a().mint, pool.token_a().decimals),
                10u64.pow(pool.token_a().decimals as u32),
            );
            let out_b = pool.calculate_swap_output(&one_a)?;
            println!(
                "swap 1 {a_sym} → {} {b_sym}  (after fee, slippage included)",
                out_b.ui_amount().round_dp(6)
            );
            // Sample swap: 1 unit of B → A
            let one_b = TokenAmount::new(
                Token::new(pool.token_b().mint, pool.token_b().decimals),
                10u64.pow(pool.token_b().decimals as u32),
            );
            let out_a = pool.calculate_swap_output(&one_b)?;
            println!("swap 1 {b_sym} → {} {a_sym}", out_a.ui_amount().round_dp(9));
        }
        Command::Whirlpool { address } => {
            let pk = Pubkey::from_str(&address)
                .map_err(|e| anyhow::anyhow!("invalid whirlpool pubkey '{address}': {e}"))?;
            let pool = rpc.fetch_orca_whirlpool(&pk).await?;
            print_whirlpool(&pool);
        }
        Command::Compare { raydium, orca } => {
            let r_pk = Pubkey::from_str(&raydium)
                .map_err(|e| anyhow::anyhow!("invalid raydium pubkey '{raydium}': {e}"))?;
            let o_pk = Pubkey::from_str(&orca)
                .map_err(|e| anyhow::anyhow!("invalid orca pubkey '{orca}': {e}"))?;
            let (r, o) = tokio::try_join!(
                rpc.fetch_raydium_pool(&r_pk),
                rpc.fetch_orca_whirlpool(&o_pk),
            )?;
            print_comparison(&r, &o);
        }
        Command::Db { command } => match command {
            DbCommand::Init { db } => {
                let store = Store::open(&db).await?;
                store.migrate().await?;
                println!("initialised SQLite store at {db}");
            }
            DbCommand::Log { raydium, orca, db } => {
                let store = Store::open(&db).await?;
                store.migrate().await?;
                let r_pk = Pubkey::from_str(&raydium)
                    .map_err(|e| anyhow::anyhow!("invalid raydium pubkey '{raydium}': {e}"))?;
                let o_pk = Pubkey::from_str(&orca)
                    .map_err(|e| anyhow::anyhow!("invalid orca pubkey '{orca}': {e}"))?;
                let (r, o) = tokio::try_join!(
                    rpc.fetch_raydium_pool(&r_pk),
                    rpc.fetch_orca_whirlpool(&o_pk),
                )?;
                persist_snapshot(&store, &r, Dex::RaydiumAmmV4).await?;
                persist_snapshot(&store, &o, Dex::OrcaWhirlpool).await?;
                println!("snapshotted prices for both pools into {db}");
                print_comparison(&r, &o);
            }
        },
    }

    Ok(())
}

fn print_whirlpool(pool: &OrcaWhirlpool) {
    let (ra, rb) = pool.reserves();
    let a_sym = known_symbol(&pool.token_a().mint).unwrap_or("A");
    let b_sym = known_symbol(&pool.token_b().mint).unwrap_or("B");
    println!("whirlpool      : {}", pool.pool_address());
    println!("program        : {}", pool.program_id());
    println!(
        "token A        : {}  decimals={}",
        label_for(&pool.token_a().mint),
        pool.token_a().decimals
    );
    println!(
        "token B        : {}  decimals={}",
        label_for(&pool.token_b().mint),
        pool.token_b().decimals
    );
    println!(
        "vault A        : {} {a_sym}  (all-tick total)",
        ra.ui_amount()
    );
    println!(
        "vault B        : {} {b_sym}  (all-tick total)",
        rb.ui_amount()
    );
    let bps = (pool.state.fee_rate as u64) * 10_000 / 1_000_000;
    println!(
        "fee_rate       : {} hundredths-of-bp  (~{} bps)",
        pool.state.fee_rate, bps
    );
    println!("tick_spacing   : {}", pool.state.tick_spacing);
    println!("liquidity (L)  : {}", pool.state.liquidity);
    println!("sqrt_price_x64 : {}", pool.state.sqrt_price_x64);
    println!("tick_current   : {}", pool.state.tick_current_index);
    println!(
        "spot price     : {} {b_sym} per {a_sym}",
        pool.price().round_dp(6)
    );
    let one_a = TokenAmount::new(
        Token::new(pool.token_a().mint, pool.token_a().decimals),
        10u64.pow(pool.token_a().decimals as u32),
    );
    if let Ok(out) = pool.calculate_swap_output(&one_a) {
        println!(
            "swap 1 {a_sym} → ~{} {b_sym}  (CPMM approx around active range)",
            out.ui_amount().round_dp(6)
        );
    }
}

fn print_comparison(r: &RaydiumPool, o: &OrcaWhirlpool) {
    if r.token_a().mint != o.token_a().mint || r.token_b().mint != o.token_b().mint {
        println!(
            "warning: token pairs differ — Raydium ({}/{}) vs Orca ({}/{})",
            label_for(&r.token_a().mint),
            label_for(&r.token_b().mint),
            label_for(&o.token_a().mint),
            label_for(&o.token_b().mint),
        );
    }
    let a_sym = known_symbol(&r.token_a().mint).unwrap_or("A");
    let b_sym = known_symbol(&r.token_b().mint).unwrap_or("B");
    let pr = r.price();
    let po = o.price();
    println!("pair           : {}/{}", a_sym, b_sym);
    println!(
        "Raydium ({}) : {} {b_sym} per {a_sym}",
        r.pool_address(),
        pr.round_dp(6)
    );
    println!(
        "Orca    ({}) : {} {b_sym} per {a_sym}",
        o.pool_address(),
        po.round_dp(6)
    );
    if pr.is_zero() || po.is_zero() {
        println!("(one of the prices is zero — can't compute spread)");
        return;
    }
    let spread = pr - po;
    let spread_bps = (spread / po) * rust_decimal::Decimal::from(10_000);
    let direction = if spread > rust_decimal::Decimal::ZERO {
        format!("buy {a_sym} on Orca, sell on Raydium")
    } else if spread < rust_decimal::Decimal::ZERO {
        format!("buy {a_sym} on Raydium, sell on Orca")
    } else {
        "no spread".to_string()
    };
    println!(
        "spread         : {} {b_sym}  ({} bps)",
        spread.round_dp(6),
        spread_bps.round_dp(2)
    );
    println!("direction      : {direction}");
}

async fn persist_snapshot<P: DexPool>(store: &Store, pool: &P, dex: Dex) -> anyhow::Result<()> {
    let row = PoolRow {
        address: *pool.pool_address(),
        program_id: *pool.program_id(),
        dex,
        token_a_mint: pool.token_a().mint,
        token_b_mint: pool.token_b().mint,
        token_a_decimals: pool.token_a().decimals,
        token_b_decimals: pool.token_b().decimals,
    };
    store.upsert_pool(&row).await?;
    let (ra, rb) = pool.reserves();
    let snap = PriceSnapshot {
        pool_address: *pool.pool_address(),
        spot_price: pool.price(),
        reserve_a_raw: ra.raw,
        reserve_b_raw: rb.raw,
    };
    store.insert_price_snapshot(&snap).await?;
    Ok(())
}

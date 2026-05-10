use std::str::FromStr;

use clap::{Parser, Subcommand};
use solana_sdk::pubkey::Pubkey;
use storm_core::Config;
use storm_solana::RpcContext;

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
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
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
    }

    Ok(())
}

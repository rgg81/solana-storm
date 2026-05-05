use clap::{Parser, Subcommand};
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
    }

    Ok(())
}

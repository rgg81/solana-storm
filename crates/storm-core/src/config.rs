use serde::Deserialize;

use crate::error::Result;

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub solana: SolanaConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SolanaConfig {
    pub rpc_url: String,
    pub ws_url: String,
    pub commitment: String,
}

impl Config {
    pub fn load() -> Result<Self> {
        let mut cfg: Self = config::Config::builder()
            .add_source(config::File::with_name("config/default"))
            .build()?
            .try_deserialize()?;

        if let Ok(url) = std::env::var("SOLANA_RPC_URL") {
            cfg.solana.rpc_url = url;
        }
        if let Ok(url) = std::env::var("SOLANA_WS_URL") {
            cfg.solana.ws_url = url;
        }
        if let Ok(c) = std::env::var("SOLANA_COMMITMENT") {
            cfg.solana.commitment = c;
        }

        Ok(cfg)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_solana_section_from_toml() {
        let cfg: Config = config::Config::builder()
            .add_source(config::File::from_str(
                r#"
                [solana]
                rpc_url = "https://api.mainnet-beta.solana.com"
                ws_url  = "wss://api.mainnet-beta.solana.com"
                commitment = "confirmed"
                "#,
                config::FileFormat::Toml,
            ))
            .build()
            .unwrap()
            .try_deserialize()
            .unwrap();

        assert_eq!(cfg.solana.commitment, "confirmed");
        assert!(cfg.solana.rpc_url.starts_with("https://"));
    }
}

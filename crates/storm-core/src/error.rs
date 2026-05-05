use thiserror::Error;

#[derive(Debug, Error)]
pub enum StormError {
    #[error("config error: {0}")]
    Config(#[from] config::ConfigError),

    #[error("rpc error: {0}")]
    Rpc(String),

    #[error("parse error: {0}")]
    Parse(String),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}

pub type Result<T> = std::result::Result<T, StormError>;

//! Survival-prediction feature extraction for graduated pump.fun tokens.
//!
//! Given a graduated token mint, [`extract_features`] fetches the needed
//! on-chain data via Solana RPC and computes a Lean-v1 [`FeatureVector`].
//!
//! The crate is split in two layers:
//!
//! * **Pure compute** — [`contract`], [`curve`], [`liquidity`], [`holders`],
//!   [`deployer`]: each takes already-fetched plain-data inputs and returns
//!   feature values. No network, no `solana-client` types — unit-tested
//!   against synthetic data.
//! * **RPC orchestration** — [`fetch`]: the only module that touches the
//!   network. It fetches accounts / the holder list / one signature page and
//!   feeds the pure functions.

pub mod contract;
pub mod curve;
pub mod deployer;
pub mod fetch;
pub mod holders;
pub mod liquidity;

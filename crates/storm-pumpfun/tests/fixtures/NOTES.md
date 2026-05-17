# Pump.fun Account Fixtures

## Token

- name: Pumpfun Pepe
- symbol: PFP
- mint: `5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump`

## Pool

- pool: `GdFCD7L8x1GiudFz1wthNHEb352k3Ni37rSwtJgMgLpT`
- pool owner (confirmed): `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` (PumpSwap program)
- pool bytes: 301

## Bonding Curve

- bonding_curve: `HLtp5EM2QRJZZXgSJqtYQ84tP8CDiziVHvFDGrEwW2wS`
- derivation: PDA with seeds `["bonding-curve", mint_bytes]` under pump.fun program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`, bump=255
- bonding_curve owner (confirmed): `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` (pump.fun program)
- bonding_curve bytes: 150

### Note on pool `creator` field vs bonding curve

The pool's `creator` field at offset 11 (`Aey8gNAUVhR2YifUkgKBUSUskgH24hWgf94m1t5TPRUn`)
is the EOA wallet that created the pool, NOT the bonding curve account. The bonding curve
is a PDA derived from the mint. The task spec's claim that "the pool's creator field is the
bonding-curve account" did not hold for this token — the correct approach is PDA derivation.

The `coin_creator` field at offset 211 (`61c7pAnp8vvfFdB1TrsTudLo7rYuUTpo1yUtEv35pdNM`)
matches the `creator` field of the bonding curve account, confirming these are consistent.

---

## PumpSwap Pool Layout (pumpswap_pool.bin — 301 bytes)

| Field                    | Start | End | Size | Type    | Value                                           |
|--------------------------|-------|-----|------|---------|-------------------------------------------------|
| discriminator            |     0 |   8 |    8 | [u8;8]  | `f19a6d0411b16dbc`                              |
| pool_bump                |     8 |   9 |    1 | u8      | 255                                             |
| index                    |     9 |  11 |    2 | u16 LE  | 0                                               |
| creator                  |    11 |  43 |   32 | Pubkey  | `Aey8gNAUVhR2YifUkgKBUSUskgH24hWgf94m1t5TPRUn` |
| base_mint                |    43 |  75 |   32 | Pubkey  | `5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump` |
| quote_mint               |    75 | 107 |   32 | Pubkey  | `So11111111111111111111111111111111111111112`    |
| lp_mint                  |   107 | 139 |   32 | Pubkey  | `4427BXCBGAxktoq5rvBWNVemVkJWHvRQbjrbUaabKbXp` |
| pool_base_token_account  |   139 | 171 |   32 | Pubkey  | `6tBDrFqenWkhhAA7RGpKBQBN8TVUWJzi3T6K81gc6KaR` |
| pool_quote_token_account |   171 | 203 |   32 | Pubkey  | `4ms9qRwh2dxPuvZmE7ndnrAsfg72zhDfY9gVo1dqGNDq` |
| lp_supply                |   203 | 211 |    8 | u64 LE  | 4193388321262                                   |
| coin_creator             |   211 | 243 |   32 | Pubkey  | `61c7pAnp8vvfFdB1TrsTudLo7rYuUTpo1yUtEv35pdNM` |
| is_mayhem_mode           |   243 | 244 |    1 | bool    | false (0)                                       |
| (padding/reserved)       |   244 | 301 |   57 | [u8;57] | all zeros                                       |

Total: 301 bytes (244 bytes of defined fields + 57 bytes trailing zeros)

---

## BondingCurve Layout (bonding_curve.bin — 150 bytes)

| Field                  | Start | End | Size | Type    | Value                                           |
|------------------------|-------|-----|------|---------|-------------------------------------------------|
| discriminator          |     0 |   8 |    8 | [u8;8]  | `17b7f83760d8ac60`                              |
| virtual_token_reserves |     8 |  16 |    8 | u64 LE  | 0                                               |
| virtual_sol_reserves   |    16 |  24 |    8 | u64 LE  | 0                                               |
| real_token_reserves    |    24 |  32 |    8 | u64 LE  | 0                                               |
| real_sol_reserves      |    32 |  40 |    8 | u64 LE  | 0                                               |
| token_total_supply     |    40 |  48 |    8 | u64 LE  | 1000000000000000                                |
| complete               |    48 |  49 |    1 | bool    | true (1) — token graduated                      |
| creator                |    49 |  81 |   32 | Pubkey  | `61c7pAnp8vvfFdB1TrsTudLo7rYuUTpo1yUtEv35pdNM` |
| (padding/reserved)     |    81 | 150 |   69 | [u8;69] | all zeros                                       |

Total: 150 bytes (81 bytes of defined fields + 69 bytes trailing zeros)

Note: All reserve fields are 0 because this token has graduated — liquidity migrated to PumpSwap.
`token_total_supply` = 1,000,000,000,000,000 = 1B tokens with 6 decimals (standard pump.fun supply).
`complete = true` confirms graduation.

---

## Capture method

Fetched via Helius mainnet RPC `getAccountInfo` with `encoding: base64`, then base64-decoded
to binary. Pool identity verified against DexScreener API (`dexId: pumpswap`). Both account
owners verified on-chain before committing.

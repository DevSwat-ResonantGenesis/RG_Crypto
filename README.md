# RG Crypto

> **Part of the [ResonantGenesis](https://dev-swat.com) platform** — Wallet, tokenization, receipts, funding, and withdrawals.

[![Status: Production](https://img.shields.io/badge/Status-Production-brightgreen.svg)]()
[![Port: 8000](https://img.shields.io/badge/Port-8000-orange.svg)]()
[![Database: PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL-336791.svg)]()
[![License: RG Source Available](https://img.shields.io/badge/License-RG%20Source%20Available-blue.svg)](LICENSE.txt)

Crypto service for platform wallet management, token operations, transaction receipts, funding flows, and withdrawal processing.

## Quick Start

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/crypto"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Mining Credit Security (Proof-of-Training)

The `POST /crypto/miner/credit` endpoint is how miners receive $RGT for verified gradient contributions. It implements **5-layer verification** to prevent unauthorized minting:

| # | Layer | Rejection |
|---|-------|-----------|
| 1 | **Internal Key Auth** | `403` — `X-Internal-Key` must match `AUTH_INTERNAL_SERVICE_KEY` |
| 2 | **Gradient Hash Required** | `400` — every credit must reference a real gradient |
| 3 | **Reward Cap** | `400` — max **500 $RGT per call** (prevents single-call inflation) |
| 4 | **HMAC-SHA256 Signature** | `403` — mining service signs `{gradient_hash}:{user_id}:{amount}:{timestamp}`, verified here |
| 5 | **Replay Protection** | `409` — `gradient_hash` stored in `mining_credits` table with UNIQUE constraint |

Additional: Signature timestamps expire after **5 minutes** (`_SIGNATURE_MAX_AGE = 300`).

**Per-call cap clarification:** Each mining cycle triggers one credit call (~100-150 $RGT). A 5-cycle session = 5 calls × ~150 = ~750 $RGT total. The 500 cap blocks single-call abuse, not legitimate multi-cycle mining.

### Models

- **`MiningCredit`** — immutable ledger of every credited gradient (replay protection)
- **`MinerStats`** — per-user cumulative mining statistics
- **`Wallet`** — user token balances (RGT)
- **`Transaction`** — all wallet transactions with metadata

## Deployment Status

- **Extracted from**: `genesis2026_production_backend/crypto_service/`
- **Server path**: `/home/deploy/RG_Crypto`
- **Docker service**: `crypto_service`

---
**Organization**: [DevSwat-ResonantGenesis](https://github.com/DevSwat-ResonantGenesis) | **Platform**: [dev-swat.com](https://dev-swat.com)

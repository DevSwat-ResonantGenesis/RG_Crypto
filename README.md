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

## Deployment Status

- **Extracted from**: `genesis2026_production_backend/crypto_service/`
- **Server path**: `/home/deploy/RG_Crypto`
- **Docker service**: `crypto_service`

---
**Organization**: [DevSwat-ResonantGenesis](https://github.com/DevSwat-ResonantGenesis) | **Platform**: [dev-swat.com](https://dev-swat.com)

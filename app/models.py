"""Crypto Service database models."""

from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, Boolean, JSON, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.sql import func

from .db import Base


class Wallet(Base):
    """User wallet for platform tokens and crypto."""
    __tablename__ = "wallets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), unique=True, index=True, nullable=False)
    
    # Balances
    token_balance = Column(Numeric(precision=36, scale=18), default=0)  # Platform tokens (RGT)
    pending_balance = Column(Numeric(precision=36, scale=18), default=0)  # Pending deposits
    locked_balance = Column(Numeric(precision=36, scale=18), default=0)  # Locked for withdrawals
    
    # External wallet addresses
    eth_address = Column(String(42), nullable=True)
    sol_address = Column(String(44), nullable=True)
    
    # Stats
    total_deposited = Column(Numeric(precision=36, scale=18), default=0)
    total_withdrawn = Column(Numeric(precision=36, scale=18), default=0)
    total_earned = Column(Numeric(precision=36, scale=18), default=0)
    total_spent = Column(Numeric(precision=36, scale=18), default=0)
    
    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    kyc_status = Column(String(32), default="none")  # none, pending, verified, rejected
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Transaction(Base):
    """All wallet transactions."""
    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wallet_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    # Transaction type
    tx_type = Column(String(32), nullable=False)  # deposit, withdrawal, purchase, sale, reward, fee, transfer
    
    # Amounts
    amount = Column(Numeric(precision=36, scale=18), nullable=False)
    fee = Column(Numeric(precision=36, scale=18), default=0)
    net_amount = Column(Numeric(precision=36, scale=18), nullable=False)
    
    # Currency
    currency = Column(String(10), default="RGT")  # RGT, USD, ETH, SOL
    
    # Status
    status = Column(String(32), default="pending")  # pending, processing, completed, failed, cancelled
    
    # Reference
    reference_type = Column(String(32), nullable=True)  # purchase, agent_run, subscription, etc.
    reference_id = Column(UUID(as_uuid=True), nullable=True)
    
    # External references
    stripe_payment_id = Column(String(128), nullable=True)
    blockchain_tx_hash = Column(String(128), nullable=True)
    
    # Metadata
    description = Column(Text, nullable=True)
    extra_metadata = Column(JSON, nullable=True)
    
    # Counterparty
    counterparty_wallet_id = Column(UUID(as_uuid=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class TokenAllocation(Base):
    """Token allocations and vesting schedules."""
    __tablename__ = "token_allocations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wallet_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    # Allocation details
    allocation_type = Column(String(32), nullable=False)  # airdrop, reward, vesting, grant
    total_amount = Column(Numeric(precision=36, scale=18), nullable=False)
    released_amount = Column(Numeric(precision=36, scale=18), default=0)
    
    # Vesting schedule
    vesting_start = Column(DateTime(timezone=True), nullable=True)
    vesting_end = Column(DateTime(timezone=True), nullable=True)
    cliff_date = Column(DateTime(timezone=True), nullable=True)
    vesting_interval = Column(String(16), nullable=True)  # daily, weekly, monthly
    
    # Status
    status = Column(String(32), default="active")  # active, completed, cancelled
    
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Receipt(Base):
    """Cryptographic receipts for all transactions."""
    __tablename__ = "receipts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id = Column(UUID(as_uuid=True), unique=True, index=True, nullable=False)
    
    # Receipt data
    receipt_number = Column(String(32), unique=True, nullable=False)
    receipt_type = Column(String(32), nullable=False)  # payment, refund, withdrawal, transfer
    
    # Parties
    payer_id = Column(UUID(as_uuid=True), nullable=True)
    payee_id = Column(UUID(as_uuid=True), nullable=True)
    
    # Amounts
    amount = Column(Numeric(precision=36, scale=18), nullable=False)
    currency = Column(String(10), nullable=False)
    
    # Cryptographic proof
    content_hash = Column(String(64), nullable=False)  # SHA-256 of receipt content
    signature = Column(Text, nullable=True)  # Platform signature
    
    # Blockchain anchoring
    blockchain_anchored = Column(Boolean, default=False)
    anchor_tx_hash = Column(String(128), nullable=True)
    anchor_block = Column(Integer, nullable=True)
    
    # Metadata
    extra_metadata = Column(JSON, nullable=True)
    
    issued_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class FundingSource(Base):
    """User funding sources (cards, bank accounts, crypto wallets)."""
    __tablename__ = "funding_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wallet_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    # Source type
    source_type = Column(String(32), nullable=False)  # card, bank, crypto_wallet
    
    # Card details (tokenized)
    stripe_payment_method_id = Column(String(128), nullable=True)
    card_last_four = Column(String(4), nullable=True)
    card_brand = Column(String(16), nullable=True)
    card_exp_month = Column(Integer, nullable=True)
    card_exp_year = Column(Integer, nullable=True)
    
    # Bank details (tokenized)
    bank_name = Column(String(128), nullable=True)
    bank_last_four = Column(String(4), nullable=True)
    
    # Crypto wallet
    crypto_address = Column(String(128), nullable=True)
    crypto_chain = Column(String(16), nullable=True)  # ethereum, solana, polygon
    
    # Status
    is_default = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    status = Column(String(32), default="active")  # active, expired, removed
    
    nickname = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class WithdrawalRequest(Base):
    """Withdrawal requests from platform."""
    __tablename__ = "withdrawal_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wallet_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    # Amount
    amount = Column(Numeric(precision=36, scale=18), nullable=False)
    fee = Column(Numeric(precision=36, scale=18), nullable=False)
    net_amount = Column(Numeric(precision=36, scale=18), nullable=False)
    currency = Column(String(10), default="RGT")
    
    # Destination
    destination_type = Column(String(32), nullable=False)  # bank, crypto_wallet
    destination_id = Column(UUID(as_uuid=True), nullable=True)  # FundingSource ID
    destination_address = Column(String(128), nullable=True)  # For crypto
    
    # Status
    status = Column(String(32), default="pending")  # pending, approved, processing, completed, rejected, cancelled
    
    # Processing
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    
    # External references
    payout_id = Column(String(128), nullable=True)  # Stripe payout ID
    blockchain_tx_hash = Column(String(128), nullable=True)
    
    # Rejection reason
    rejection_reason = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TokenPrice(Base):
    """Token price history."""
    __tablename__ = "token_prices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    token = Column(String(10), nullable=False)  # RGT, ETH, SOL
    price_usd = Column(Numeric(precision=18, scale=8), nullable=False)
    
    # Market data
    volume_24h = Column(Numeric(precision=36, scale=18), nullable=True)
    market_cap = Column(Numeric(precision=36, scale=18), nullable=True)
    change_24h = Column(Float, nullable=True)
    
    source = Column(String(32), nullable=True)  # internal, coingecko, etc.
    
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PaymentIntent(Base):
    """Payment intents for deposits."""
    __tablename__ = "payment_intents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wallet_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    # Amount
    amount = Column(Numeric(precision=18, scale=2), nullable=False)
    currency = Column(String(3), default="USD")
    tokens_to_receive = Column(Numeric(precision=36, scale=18), nullable=True)
    
    # Stripe
    stripe_payment_intent_id = Column(String(128), unique=True, nullable=True)
    stripe_client_secret = Column(String(256), nullable=True)
    
    # Status
    status = Column(String(32), default="created")  # created, processing, succeeded, failed, cancelled
    
    # Metadata
    extra_metadata = Column(JSON, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class MiningCredit(Base):
    """
    Proof-of-training ledger — every credited gradient is recorded here.
    Prevents replay attacks: each gradient_hash can only be credited ONCE.
    Similar to Bitcoin's UTXO model where each coinbase reward is unique.
    """
    __tablename__ = "mining_credits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gradient_hash = Column(String(128), unique=True, index=True, nullable=False)
    user_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    rgt_amount = Column(Numeric(precision=36, scale=18), nullable=False)
    task_id = Column(String(128), nullable=True)
    global_step = Column(Integer, default=0)
    hmac_signature = Column(String(128), nullable=False)  # HMAC proof from mining service
    chain_verified = Column(Boolean, default=False)  # Verified on external blockchain
    chain_tx_hash = Column(String(128), nullable=True)  # Blockchain tx hash
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MinerStats(Base):
    """Per-user miner dashboard statistics."""
    __tablename__ = "miner_stats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), unique=True, index=True, nullable=False)
    wallet_id = Column(UUID(as_uuid=True), index=True, nullable=True)

    # Core metrics
    trust_score = Column(Float, default=0.0)
    rgt_earned = Column(Numeric(precision=36, scale=18), default=0)
    tasks_completed = Column(Integer, default=0)
    samples_processed = Column(Integer, default=0)

    # Classification
    tier = Column(String(32), default="miner")  # validator_miner, core_miner, miner
    verification_rate = Column(Float, default=0.0)
    status = Column(String(16), default="idle")  # active, idle, suspended

    # Network context
    current_epoch = Column(Integer, default=0)
    total_miners = Column(Integer, default=0)
    your_rank = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

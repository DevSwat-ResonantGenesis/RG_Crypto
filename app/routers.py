"""Crypto Service API routers."""

import hashlib
import hmac as _hmac
import logging
import time as _time
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
import re
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import (
    Wallet, Transaction, Receipt, FundingSource,
    WithdrawalRequest, PaymentIntent, TokenAllocation, MinerStats, MiningCredit
)

logger = logging.getLogger(__name__)
from .wallet import wallet_manager, funding_manager
from .tokenization import token_manager, reward_engine
from .payments import payment_processor, withdrawal_processor


router = APIRouter(prefix="/crypto", tags=["crypto"])


# ============== Request/Response Models ==============

class WalletResponse(BaseModel):
    id: str
    user_id: str
    token_balance: str
    pending_balance: str
    locked_balance: str
    is_verified: bool
    kyc_status: str


class BalanceResponse(BaseModel):
    available: str
    pending: str
    locked: str
    total: str


class DepositRequest(BaseModel):
    amount_usd: float = Field(..., gt=0)


class DepositResponse(BaseModel):
    intent_id: str
    amount_usd: str
    tokens_to_receive: str
    client_secret: Optional[str] = None
    status: str


class WithdrawalRequestCreate(BaseModel):
    amount: float = Field(..., gt=0)
    destination_type: str  # crypto_wallet, bank
    destination_id: Optional[str] = None
    destination_address: Optional[str] = None


class WithdrawalResponse(BaseModel):
    id: str
    amount: str
    fee: str
    net_amount: str
    destination_type: str
    status: str


class TransactionResponse(BaseModel):
    id: str
    tx_type: str
    amount: str
    fee: str
    net_amount: str
    currency: str
    status: str
    description: Optional[str]
    created_at: str


class FundingSourceCreate(BaseModel):
    source_type: str  # card, crypto_wallet
    stripe_payment_method_id: Optional[str] = None
    crypto_address: Optional[str] = None
    crypto_chain: Optional[str] = None
    nickname: Optional[str] = None


class FundingSourceResponse(BaseModel):
    id: str
    source_type: str
    card_last_four: Optional[str] = None
    card_brand: Optional[str] = None
    crypto_address: Optional[str] = None
    crypto_chain: Optional[str] = None
    is_default: bool
    is_verified: bool


class TransferRequest(BaseModel):
    to: str = Field(..., description="Recipient: email, user UUID, or crypto_hash")
    to_user_id: Optional[str] = None  # legacy compat
    amount: float = Field(..., gt=0)
    description: Optional[str] = None


class TokenStatsResponse(BaseModel):
    total_supply: str
    circulating_supply: str
    locked_supply: str
    current_price_usd: str
    market_cap_usd: str


# ============== Wallet Endpoints ==============

@router.post("/wallet", response_model=WalletResponse, status_code=status.HTTP_201_CREATED)
async def create_wallet(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a wallet for the current user."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    wallet = await wallet_manager.create_wallet(user_id, session)

    return WalletResponse(
        id=str(wallet.id),
        user_id=str(wallet.user_id),
        token_balance=str(wallet.token_balance),
        pending_balance=str(wallet.pending_balance),
        locked_balance=str(wallet.locked_balance),
        is_verified=wallet.is_verified,
        kyc_status=wallet.kyc_status,
    )


@router.get("/wallet", response_model=WalletResponse)
async def get_wallet(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get the current user's wallet."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    wallet = await wallet_manager.get_wallet(user_id, session)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    return WalletResponse(
        id=str(wallet.id),
        user_id=str(wallet.user_id),
        token_balance=str(wallet.token_balance),
        pending_balance=str(wallet.pending_balance),
        locked_balance=str(wallet.locked_balance),
        is_verified=wallet.is_verified,
        kyc_status=wallet.kyc_status,
    )


@router.get("/wallets")
async def list_wallets(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List wallets (alias for /wallet for compatibility)."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    wallet = await wallet_manager.get_wallet(user_id, session)
    if not wallet:
        return {"wallets": []}

    return {
        "wallets": [{
            "id": str(wallet.id),
            "user_id": str(wallet.user_id),
            "token_balance": str(wallet.token_balance),
            "pending_balance": str(wallet.pending_balance),
            "locked_balance": str(wallet.locked_balance),
            "is_verified": wallet.is_verified,
            "kyc_status": wallet.kyc_status,
        }]
    }


@router.get("/wallet/balance", response_model=BalanceResponse)
async def get_balance(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get wallet balance breakdown."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    wallet = await wallet_manager.get_wallet(user_id, session)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    balance = await wallet_manager.get_balance(str(wallet.id), session)

    return BalanceResponse(
        available=str(balance.get("available", 0)),
        pending=str(balance.get("pending", 0)),
        locked=str(balance.get("locked", 0)),
        total=str(balance.get("total", 0)),
    )


# ============== Deposit Endpoints ==============

@router.post("/deposit", response_model=DepositResponse)
async def create_deposit(
    payload: DepositRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a deposit intent."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    wallet = await wallet_manager.get_wallet(user_id, session)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    intent = await payment_processor.create_deposit_intent(
        wallet_id=str(wallet.id),
        amount_usd=Decimal(str(payload.amount_usd)),
        db_session=session,
    )

    return DepositResponse(
        intent_id=str(intent.id),
        amount_usd=str(intent.amount),
        tokens_to_receive=str(intent.tokens_to_receive),
        client_secret=intent.stripe_client_secret,
        status=intent.status,
    )


@router.post("/deposit/webhook")
async def deposit_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Handle Stripe webhook for deposits."""
    payload = await request.json()
    signature = request.headers.get("stripe-signature", "")

    result = await payment_processor.process_webhook(payload, signature, session)
    return result


# ============== Withdrawal Endpoints ==============

@router.post("/withdraw", response_model=WithdrawalResponse)
async def request_withdrawal(
    payload: WithdrawalRequestCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Request a withdrawal."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    wallet = await wallet_manager.get_wallet(user_id, session)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    try:
        withdrawal = await withdrawal_processor.request_withdrawal(
            wallet_id=str(wallet.id),
            amount=Decimal(str(payload.amount)),
            destination_type=payload.destination_type,
            destination_id=payload.destination_id,
            destination_address=payload.destination_address,
            db_session=session,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return WithdrawalResponse(
        id=str(withdrawal.id),
        amount=str(withdrawal.amount),
        fee=str(withdrawal.fee),
        net_amount=str(withdrawal.net_amount),
        destination_type=withdrawal.destination_type,
        status=withdrawal.status,
    )


@router.get("/withdrawals", response_model=List[WithdrawalResponse])
async def list_withdrawals(
    request: Request,
    status_filter: Optional[str] = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """List user's withdrawal requests."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    wallet = await wallet_manager.get_wallet(user_id, session)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    stmt = select(WithdrawalRequest).where(WithdrawalRequest.wallet_id == wallet.id)
    if status_filter:
        stmt = stmt.where(WithdrawalRequest.status == status_filter)
    stmt = stmt.order_by(WithdrawalRequest.created_at.desc()).limit(limit)

    result = await session.execute(stmt)
    withdrawals = result.scalars().all()

    return [
        WithdrawalResponse(
            id=str(w.id),
            amount=str(w.amount),
            fee=str(w.fee),
            net_amount=str(w.net_amount),
            destination_type=w.destination_type,
            status=w.status,
        )
        for w in withdrawals
    ]


@router.post("/withdrawals/{withdrawal_id}/cancel")
async def cancel_withdrawal(
    withdrawal_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Cancel a pending withdrawal."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        withdrawal = await withdrawal_processor.cancel_withdrawal(
            request_id=withdrawal_id,
            user_id=user_id,
            db_session=session,
        )
        return {"status": "cancelled", "id": str(withdrawal.id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============== Transaction Endpoints ==============

@router.get("/transactions", response_model=List[TransactionResponse])
async def list_transactions(
    request: Request,
    tx_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """List user's transactions."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    wallet = await wallet_manager.get_wallet(user_id, session)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    stmt = select(Transaction).where(Transaction.wallet_id == wallet.id)
    if tx_type:
        stmt = stmt.where(Transaction.tx_type == tx_type)
    stmt = stmt.order_by(Transaction.created_at.desc()).offset(offset).limit(limit)

    result = await session.execute(stmt)
    transactions = result.scalars().all()

    return [
        TransactionResponse(
            id=str(t.id),
            tx_type=t.tx_type,
            amount=str(t.amount),
            fee=str(t.fee),
            net_amount=str(t.net_amount),
            currency=t.currency,
            status=t.status,
            description=t.description,
            created_at=t.created_at.isoformat(),
        )
        for t in transactions
    ]


@router.get("/transactions/{tx_id}", response_model=TransactionResponse)
async def get_transaction(
    tx_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get a specific transaction."""
    result = await session.execute(
        select(Transaction).where(Transaction.id == tx_id)
    )
    tx = result.scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return TransactionResponse(
        id=str(tx.id),
        tx_type=tx.tx_type,
        amount=str(tx.amount),
        fee=str(tx.fee),
        net_amount=str(tx.net_amount),
        currency=tx.currency,
        status=tx.status,
        description=tx.description,
        created_at=tx.created_at.isoformat(),
    )


# ============== Transfer Endpoints ==============

def _is_uuid(s: str) -> bool:
    try:
        _uuid.UUID(s)
        return True
    except ValueError:
        return False


def _is_email(s: str) -> bool:
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', s))


def _is_crypto_hash(s: str) -> bool:
    clean = s.lower().removeprefix('0x')
    return len(clean) == 64 and all(c in '0123456789abcdef' for c in clean)


async def _resolve_recipient_user_id(
    identifier: str, session: AsyncSession
) -> Optional[str]:
    """Resolve email, crypto_hash, or UUID to a user_id string."""
    identifier = identifier.strip()
    if _is_uuid(identifier):
        return identifier
    if _is_email(identifier):
        result = await session.execute(
            text("SELECT id FROM users WHERE LOWER(email) = LOWER(:email) LIMIT 1"),
            {"email": identifier},
        )
        row = result.fetchone()
        return str(row[0]) if row else None
    if _is_crypto_hash(identifier):
        clean = identifier.lower().removeprefix('0x')
        result = await session.execute(
            text("SELECT id FROM users WHERE crypto_hash = :ch LIMIT 1"),
            {"ch": clean},
        )
        row = result.fetchone()
        return str(row[0]) if row else None
    return None


@router.post("/transfer")
async def transfer_tokens(
    payload: TransferRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Transfer tokens to another user by email, user ID, or crypto_hash."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    from_wallet = await wallet_manager.get_wallet(user_id, session)
    if not from_wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    # Resolve recipient: accept email, UUID, or crypto_hash
    recipient_identifier = payload.to or payload.to_user_id
    if not recipient_identifier:
        raise HTTPException(status_code=400, detail="Recipient is required (email, user ID, or blockchain hash)")

    to_user_id = await _resolve_recipient_user_id(recipient_identifier, session)
    if not to_user_id:
        raise HTTPException(
            status_code=404,
            detail=f"Recipient not found. You can use an email address, user ID, or blockchain hash (0x...)."
        )

    if to_user_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot transfer to yourself")

    to_wallet = await wallet_manager.get_wallet(to_user_id, session)
    if not to_wallet:
        raise HTTPException(status_code=404, detail="Recipient has no wallet yet. They need to create one first.")

    try:
        debit_tx, credit_tx = await wallet_manager.transfer(
            from_wallet_id=str(from_wallet.id),
            to_wallet_id=str(to_wallet.id),
            amount=Decimal(str(payload.amount)),
            description=payload.description or "Token transfer",
            db_session=session,
        )
        return {
            "status": "completed",
            "transaction_id": str(debit_tx.id),
            "amount": str(payload.amount),
            "to_user_id": to_user_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============== Funding Source Endpoints ==============

@router.post("/funding-sources", response_model=FundingSourceResponse)
async def add_funding_source(
    payload: FundingSourceCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Add a funding source."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    wallet = await wallet_manager.get_wallet(user_id, session)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    if payload.source_type == "card" and payload.stripe_payment_method_id:
        source = await funding_manager.add_card(
            wallet_id=str(wallet.id),
            stripe_payment_method_id=payload.stripe_payment_method_id,
            card_details={},  # Would be fetched from Stripe
            db_session=session,
        )
    elif payload.source_type == "crypto_wallet" and payload.crypto_address:
        source = await funding_manager.add_crypto_wallet(
            wallet_id=str(wallet.id),
            address=payload.crypto_address,
            chain=payload.crypto_chain or "ethereum",
            nickname=payload.nickname,
            db_session=session,
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid funding source")

    return FundingSourceResponse(
        id=str(source.id),
        source_type=source.source_type,
        card_last_four=source.card_last_four,
        card_brand=source.card_brand,
        crypto_address=source.crypto_address,
        crypto_chain=source.crypto_chain,
        is_default=source.is_default,
        is_verified=source.is_verified,
    )


@router.get("/funding-sources", response_model=List[FundingSourceResponse])
async def list_funding_sources(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List user's funding sources."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    wallet = await wallet_manager.get_wallet(user_id, session)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    sources = await funding_manager.list_sources(str(wallet.id), session)

    return [
        FundingSourceResponse(
            id=str(s.id),
            source_type=s.source_type,
            card_last_four=s.card_last_four,
            card_brand=s.card_brand,
            crypto_address=s.crypto_address,
            crypto_chain=s.crypto_chain,
            is_default=s.is_default,
            is_verified=s.is_verified,
        )
        for s in sources
    ]


@router.delete("/funding-sources/{source_id}")
async def remove_funding_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Remove a funding source."""
    removed = await funding_manager.remove_source(source_id, session)
    if not removed:
        raise HTTPException(status_code=404, detail="Funding source not found")
    return {"status": "removed", "id": source_id}


# ============== Receipt Endpoints ==============

@router.get("/receipts/{receipt_id}")
async def get_receipt(
    receipt_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get a receipt by ID."""
    result = await session.execute(
        select(Receipt).where(Receipt.id == receipt_id)
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    return {
        "id": str(receipt.id),
        "receipt_number": receipt.receipt_number,
        "receipt_type": receipt.receipt_type,
        "amount": str(receipt.amount),
        "currency": receipt.currency,
        "content_hash": receipt.content_hash,
        "blockchain_anchored": receipt.blockchain_anchored,
        "anchor_tx_hash": receipt.anchor_tx_hash,
        "issued_at": receipt.issued_at.isoformat(),
    }


@router.get("/receipts/transaction/{tx_id}")
async def get_receipt_by_transaction(
    tx_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get receipt for a transaction."""
    result = await session.execute(
        select(Receipt).where(Receipt.transaction_id == tx_id)
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    return {
        "id": str(receipt.id),
        "receipt_number": receipt.receipt_number,
        "receipt_type": receipt.receipt_type,
        "amount": str(receipt.amount),
        "currency": receipt.currency,
        "content_hash": receipt.content_hash,
        "blockchain_anchored": receipt.blockchain_anchored,
        "issued_at": receipt.issued_at.isoformat(),
    }


# ============== Token Stats Endpoints ==============

@router.get("/token/stats", response_model=TokenStatsResponse)
async def get_token_stats(
    session: AsyncSession = Depends(get_session),
):
    """Get platform token statistics."""
    stats = await token_manager.get_token_stats(session)

    return TokenStatsResponse(
        total_supply=stats["total_supply"],
        circulating_supply=stats["circulating_supply"],
        locked_supply=stats["locked_supply"],
        current_price_usd=stats["current_price_usd"],
        market_cap_usd=stats["market_cap_usd"],
    )


@router.get("/token/price")
async def get_token_price(
    session: AsyncSession = Depends(get_session),
):
    """Get current token price."""
    price = await token_manager.get_current_price(db_session=session)
    return {"token": "RGT", "price_usd": str(price)}


# ============== Reward Endpoint ==============

class RewardRequest(BaseModel):
    user_id: str
    amount: float
    reason: str = "Platform reward"
    source: str = "system"


@router.post("/wallet/reward")
async def reward_user(
    payload: RewardRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Award RGT tokens to a user as a reward (system/marketplace/agent)."""
    from decimal import Decimal

    # Only allow system or internal service calls
    caller_role = request.headers.get("x-user-role", "")
    if caller_role not in ("system", "platform_dev", "platform_owner"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Find or create the user's wallet
    wallet = await wallet_manager.get_wallet(payload.user_id, session)
    if not wallet:
        wallet = await wallet_manager.create_wallet(payload.user_id, session)

    # Credit the reward
    tx = await wallet_manager.credit(
        wallet_id=str(wallet.id),
        amount=Decimal(str(payload.amount)),
        tx_type="reward",
        description=payload.reason,
        reference_type=payload.source,
        metadata={"source": payload.source, "reason": payload.reason},
        db_session=session,
    )

    return {
        "status": "rewarded",
        "user_id": payload.user_id,
        "amount": str(payload.amount),
        "tx_id": str(tx.id),
        "new_balance": str(wallet.token_balance),
    }


# ============== Miner Stats Endpoints ==============

class MinerStatsResponse(BaseModel):
    trust_score: float
    rgt_earned: str
    tasks_completed: int
    samples_processed: int
    tier: str
    verification_rate: float
    status: str
    current_epoch: int
    total_miners: int
    your_rank: int


@router.get("/miner/stats", response_model=MinerStatsResponse)
async def get_miner_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get the current user's miner dashboard stats."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    result = await session.execute(
        select(MinerStats).where(MinerStats.user_id == user_id)
    )
    stats = result.scalar_one_or_none()

    if not stats:
        # Auto-create a default row for the user
        wallet = await wallet_manager.get_wallet(user_id, session)
        stats = MinerStats(
            user_id=user_id,
            wallet_id=str(wallet.id) if wallet else None,
        )
        session.add(stats)
        await session.commit()
        await session.refresh(stats)

    return MinerStatsResponse(
        trust_score=stats.trust_score or 0.0,
        rgt_earned=str(stats.rgt_earned or 0),
        tasks_completed=stats.tasks_completed or 0,
        samples_processed=stats.samples_processed or 0,
        tier=stats.tier or "miner",
        verification_rate=stats.verification_rate or 0.0,
        status=stats.status or "idle",
        current_epoch=stats.current_epoch or 0,
        total_miners=stats.total_miners or 0,
        your_rank=stats.your_rank or 0,
    )


class MinerCreditRequest(BaseModel):
    user_id: Optional[str] = None
    email: Optional[str] = None
    rgt_amount: float = 0
    tasks_delta: int = 1
    samples_delta: int = 0
    trust_score: Optional[float] = None
    tier: Optional[str] = None
    gradient_hash: Optional[str] = None
    task_id: Optional[str] = None
    global_step: int = 0
    timestamp: Optional[int] = None
    signature: Optional[str] = None


# ── Proof-of-Training constants ──
# Max $RGT that can be credited in a single call (Year-1 reward schedule)
_MAX_REWARD_PER_CREDIT = 500.0
# HMAC signature validity window (seconds) — reject stale requests
_SIGNATURE_MAX_AGE = 300  # 5 minutes


def _verify_credit_signature(
    gradient_hash: str, user_id: str, rgt_amount: float,
    timestamp: int, signature: str, internal_key: str,
) -> bool:
    """
    Verify HMAC-SHA256 proof-of-training.
    The mining service signs: "{gradient_hash}:{user_id}:{rgt_amount}:{timestamp}"
    with the shared INTERNAL_SERVICE_KEY. This proves the credit request
    originated from the mining service which actually verified the gradient.
    """
    msg = f"{gradient_hash}:{user_id}:{rgt_amount}:{timestamp}"
    expected = _hmac.new(
        internal_key.encode(), msg.encode(), hashlib.sha256,
    ).hexdigest()
    return _hmac.compare_digest(expected, signature)


@router.post("/miner/credit")
async def credit_miner(
    payload: MinerCreditRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """
    Internal endpoint: Mining service credits a miner after gradient accepted.

    Security (proof-of-training, like Bitcoin's coinbase tx):
      1. HMAC-SHA256 signature — only mining service can sign
      2. Replay protection — each gradient_hash credited exactly once
      3. Reward cap — max per-credit limit prevents inflation
      4. Timestamp window — stale requests rejected
      5. Internal key auth — only reachable within Docker network
    """
    import os

    internal_key = os.getenv("AUTH_INTERNAL_SERVICE_KEY", "")

    # ── Layer 1: Internal service key auth ──
    caller_key = request.headers.get("x-internal-key", "")
    if not internal_key or caller_key != internal_key:
        raise HTTPException(status_code=403, detail="Internal service auth required")

    # ── Layer 2: Require gradient_hash (proof-of-training anchor) ──
    if not payload.gradient_hash:
        raise HTTPException(status_code=400, detail="gradient_hash required (proof-of-training)")

    # ── Layer 3: Reward cap ──
    if payload.rgt_amount <= 0:
        raise HTTPException(status_code=400, detail="rgt_amount must be positive")
    if payload.rgt_amount > _MAX_REWARD_PER_CREDIT:
        raise HTTPException(
            status_code=400,
            detail=f"rgt_amount {payload.rgt_amount} exceeds max {_MAX_REWARD_PER_CREDIT} per credit",
        )

    # ── Layer 4: HMAC signature verification ──
    if not payload.signature or not payload.timestamp:
        raise HTTPException(status_code=400, detail="signature and timestamp required")

    now = int(_time.time())
    if abs(now - payload.timestamp) > _SIGNATURE_MAX_AGE:
        raise HTTPException(status_code=400, detail="Signature expired (timestamp too old)")

    # Resolve user_id first (needed for signature verification)
    user_id = payload.user_id
    if not user_id and payload.email:
        row = await session.execute(
            text("SELECT id FROM users WHERE email = :email LIMIT 1"),
            {"email": payload.email},
        )
        result = row.first()
        if result:
            user_id = str(result[0])

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id or email required")

    if not _verify_credit_signature(
        payload.gradient_hash, payload.user_id or "",
        payload.rgt_amount, payload.timestamp,
        payload.signature, internal_key,
    ):
        logger.warning(f"HMAC verification FAILED for gradient {payload.gradient_hash[:16]}...")
        raise HTTPException(status_code=403, detail="Invalid proof-of-training signature")

    # ── Layer 5: Replay protection — gradient_hash can only be credited ONCE ──
    existing = await session.execute(
        select(MiningCredit).where(MiningCredit.gradient_hash == payload.gradient_hash)
    )
    if existing.scalar_one_or_none():
        logger.warning(f"Replay attempt blocked: gradient {payload.gradient_hash[:16]}... already credited")
        raise HTTPException(status_code=409, detail="Gradient already credited (replay blocked)")

    # ── All checks passed — record the credit in the ledger ──
    credit_record = MiningCredit(
        gradient_hash=payload.gradient_hash,
        user_id=user_id,
        rgt_amount=Decimal(str(payload.rgt_amount)),
        task_id=payload.task_id,
        global_step=payload.global_step,
        hmac_signature=payload.signature,
    )
    session.add(credit_record)

    # Update or create MinerStats
    result = await session.execute(
        select(MinerStats).where(MinerStats.user_id == user_id)
    )
    stats = result.scalar_one_or_none()

    if not stats:
        wallet = await wallet_manager.get_wallet(user_id, session)
        stats = MinerStats(
            user_id=user_id,
            wallet_id=str(wallet.id) if wallet else None,
        )
        session.add(stats)

    # Accumulate stats
    stats.rgt_earned = (stats.rgt_earned or Decimal("0")) + Decimal(str(payload.rgt_amount))
    stats.tasks_completed = (stats.tasks_completed or 0) + payload.tasks_delta
    stats.samples_processed = (stats.samples_processed or 0) + payload.samples_delta
    stats.current_epoch = payload.global_step
    stats.status = "active"
    if payload.trust_score is not None:
        stats.trust_score = payload.trust_score
    if payload.tier:
        stats.tier = payload.tier

    # Credit the wallet balance (actual $RGT)
    try:
        wallet = await wallet_manager.get_wallet(user_id, session)
        if not wallet:
            wallet = await wallet_manager.create_wallet(user_id, session)
        await wallet_manager.credit(
            wallet_id=str(wallet.id),
            amount=Decimal(str(payload.rgt_amount)),
            tx_type="reward",
            description=f"Mining reward: task {payload.task_id or 'unknown'}",
            reference_type="mining",
            metadata={
                "gradient_hash": payload.gradient_hash,
                "task_id": payload.task_id,
                "global_step": payload.global_step,
                "hmac_verified": True,
            },
            db_session=session,
        )
    except Exception as e:
        logger.error(f"Wallet credit failed for {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Wallet credit failed")

    await session.commit()
    await session.refresh(stats)

    logger.info(
        f"Mining credit: {payload.rgt_amount} RGT → {user_id} "
        f"(gradient={payload.gradient_hash[:16]}..., step={payload.global_step})"
    )

    return {
        "status": "credited",
        "user_id": user_id,
        "rgt_earned": str(stats.rgt_earned),
        "tasks_completed": stats.tasks_completed,
        "samples_processed": stats.samples_processed,
    }


# ============== ML Memory Stats Endpoint ==============

@router.get("/miner/ml-stats")
async def get_ml_memory_stats(
    request: Request,
):
    """Fetch training model stats from the ML memory database."""
    import os
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession as _AS
    from sqlalchemy.orm import sessionmaker as _sm
    from sqlalchemy import text

    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    ml_db_url = os.getenv("ML_DATABASE_URL", "")
    if not ml_db_url:
        return {
            "connected": False,
            "message": "ML_DATABASE_URL not configured",
            "training_runs": 0,
            "latest_loss": None,
            "latest_epoch": None,
        }

    try:
        ml_engine = create_async_engine(ml_db_url, pool_pre_ping=True)
        _session_factory = _sm(ml_engine, class_=_AS, expire_on_commit=False)
        async with _session_factory() as ml_session:
            # Query latest training metrics for the user
            row = await ml_session.execute(
                text(
                    "SELECT COUNT(*) as runs, "
                    "MIN(loss) as best_loss, "
                    "MAX(epoch) as latest_epoch "
                    "FROM training_metrics "
                    "WHERE user_id = :uid"
                ),
                {"uid": user_id},
            )
            r = row.mappings().first()
            return {
                "connected": True,
                "training_runs": r["runs"] if r else 0,
                "best_loss": float(r["best_loss"]) if r and r["best_loss"] else None,
                "latest_epoch": int(r["latest_epoch"]) if r and r["latest_epoch"] else None,
            }
    except Exception as e:
        return {
            "connected": False,
            "message": str(e),
            "training_runs": 0,
            "best_loss": None,
            "latest_epoch": None,
        }


# ============== Health Endpoint ==============

@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"service": "crypto", "status": "ok"}

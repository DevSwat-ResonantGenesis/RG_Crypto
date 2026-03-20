"""Crypto Service API routers."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import (
    Wallet, Transaction, Receipt, FundingSource,
    WithdrawalRequest, PaymentIntent, TokenAllocation
)
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
    to_user_id: str
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

@router.post("/transfer")
async def transfer_tokens(
    payload: TransferRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Transfer tokens to another user."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    from_wallet = await wallet_manager.get_wallet(user_id, session)
    if not from_wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    to_wallet = await wallet_manager.get_wallet(payload.to_user_id, session)
    if not to_wallet:
        raise HTTPException(status_code=404, detail="Recipient wallet not found")

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


# ============== Health Endpoint ==============

@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"service": "crypto", "status": "ok"}

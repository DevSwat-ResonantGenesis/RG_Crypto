"""Payment processing and withdrawals."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Wallet, Transaction, PaymentIntent, WithdrawalRequest,
    FundingSource, Receipt
)
from .wallet import wallet_manager
from .tokenization import token_manager
from .config import settings


class PaymentProcessor:
    """Handles payment processing via Stripe."""

    async def create_deposit_intent(
        self,
        wallet_id: str,
        amount_usd: Decimal,
        db_session: AsyncSession,
    ) -> PaymentIntent:
        """Create a payment intent for depositing funds."""
        # Calculate tokens to receive
        tokens = await token_manager.usd_to_tokens(amount_usd, db_session)

        intent = PaymentIntent(
            wallet_id=wallet_id,
            amount=amount_usd,
            currency="USD",
            tokens_to_receive=tokens,
            status="created",
        )
        db_session.add(intent)
        await db_session.commit()
        await db_session.refresh(intent)

        # In production, create Stripe PaymentIntent
        if settings.STRIPE_SECRET_KEY:
            try:
                import stripe
                stripe.api_key = settings.STRIPE_SECRET_KEY

                stripe_intent = stripe.PaymentIntent.create(
                    amount=int(amount_usd * 100),  # Cents
                    currency="usd",
                    metadata={
                        "wallet_id": str(wallet_id),
                        "intent_id": str(intent.id),
                        "tokens": str(tokens),
                    },
                )
                intent.stripe_payment_intent_id = stripe_intent.id
                intent.stripe_client_secret = stripe_intent.client_secret
                await db_session.commit()
            except Exception as e:
                intent.status = "failed"
                intent.metadata = {"error": str(e)}
                await db_session.commit()

        return intent

    async def confirm_deposit(
        self,
        intent_id: str,
        stripe_payment_intent_id: str,
        db_session: AsyncSession,
    ) -> Transaction:
        """Confirm a deposit after successful payment."""
        result = await db_session.execute(
            select(PaymentIntent).where(PaymentIntent.id == intent_id)
        )
        intent = result.scalar_one_or_none()
        if not intent:
            raise ValueError("Payment intent not found")

        if intent.status == "succeeded":
            raise ValueError("Payment already processed")

        # Credit tokens to wallet
        tx = await wallet_manager.credit(
            wallet_id=str(intent.wallet_id),
            amount=intent.tokens_to_receive,
            tx_type="deposit",
            description=f"Deposit of ${intent.amount} USD",
            reference_type="payment_intent",
            reference_id=str(intent.id),
            metadata={
                "usd_amount": str(intent.amount),
                "stripe_payment_intent_id": stripe_payment_intent_id,
            },
            db_session=db_session,
        )

        intent.status = "succeeded"
        intent.completed_at = datetime.utcnow()
        await db_session.commit()

        return tx

    async def process_webhook(
        self,
        payload: Dict[str, Any],
        signature: str,
        db_session: AsyncSession,
    ) -> Dict[str, Any]:
        """Process Stripe webhook events."""
        event_type = payload.get("type")
        data = payload.get("data", {}).get("object", {})

        if event_type == "payment_intent.succeeded":
            intent_id = data.get("metadata", {}).get("intent_id")
            if intent_id:
                await self.confirm_deposit(
                    intent_id=intent_id,
                    stripe_payment_intent_id=data.get("id"),
                    db_session=db_session,
                )
                return {"status": "processed", "event": event_type}

        elif event_type == "payment_intent.payment_failed":
            intent_id = data.get("metadata", {}).get("intent_id")
            if intent_id:
                result = await db_session.execute(
                    select(PaymentIntent).where(PaymentIntent.id == intent_id)
                )
                intent = result.scalar_one_or_none()
                if intent:
                    intent.status = "failed"
                    await db_session.commit()
                return {"status": "processed", "event": event_type}

        return {"status": "ignored", "event": event_type}


class WithdrawalProcessor:
    """Handles withdrawal processing."""

    async def request_withdrawal(
        self,
        wallet_id: str,
        amount: Decimal,
        destination_type: str,
        destination_id: Optional[str] = None,
        destination_address: Optional[str] = None,
        db_session: AsyncSession = None,
    ) -> WithdrawalRequest:
        """Create a withdrawal request."""
        # Validate minimum amount
        if amount < Decimal(str(settings.MIN_WITHDRAWAL_AMOUNT)):
            raise ValueError(f"Minimum withdrawal is {settings.MIN_WITHDRAWAL_AMOUNT} tokens")

        # Check balance
        result = await db_session.execute(
            select(Wallet).where(Wallet.id == wallet_id)
        )
        wallet = result.scalar_one_or_none()
        if not wallet:
            raise ValueError("Wallet not found")

        if wallet.token_balance < amount:
            raise ValueError("Insufficient balance")

        # Calculate fee
        fee = amount * Decimal(str(settings.WITHDRAWAL_FEE_PERCENT)) / 100
        net_amount = amount - fee

        # Lock the balance
        locked = await wallet_manager.lock_balance(wallet_id, amount, db_session)
        if not locked:
            raise ValueError("Failed to lock balance")

        # Create withdrawal request
        request = WithdrawalRequest(
            wallet_id=wallet_id,
            amount=amount,
            fee=fee,
            net_amount=net_amount,
            currency=settings.PLATFORM_TOKEN_SYMBOL,
            destination_type=destination_type,
            destination_id=destination_id,
            destination_address=destination_address,
            status="pending",
        )
        db_session.add(request)
        await db_session.commit()
        await db_session.refresh(request)

        return request

    async def approve_withdrawal(
        self,
        request_id: str,
        approver_id: str,
        db_session: AsyncSession,
    ) -> WithdrawalRequest:
        """Approve a withdrawal request."""
        result = await db_session.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.id == request_id)
        )
        request = result.scalar_one_or_none()
        if not request:
            raise ValueError("Withdrawal request not found")

        if request.status != "pending":
            raise ValueError("Request is not pending")

        request.status = "approved"
        request.approved_by = approver_id
        request.approved_at = datetime.utcnow()
        await db_session.commit()

        return request

    async def process_withdrawal(
        self,
        request_id: str,
        db_session: AsyncSession,
    ) -> WithdrawalRequest:
        """Process an approved withdrawal."""
        result = await db_session.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.id == request_id)
        )
        request = result.scalar_one_or_none()
        if not request:
            raise ValueError("Withdrawal request not found")

        if request.status != "approved":
            raise ValueError("Request is not approved")

        request.status = "processing"
        await db_session.commit()

        try:
            if request.destination_type == "crypto_wallet":
                # Process crypto withdrawal
                tx_hash = await self._process_crypto_withdrawal(request, db_session)
                request.blockchain_tx_hash = tx_hash
            elif request.destination_type == "bank":
                # Process bank withdrawal via Stripe
                payout_id = await self._process_bank_withdrawal(request, db_session)
                request.payout_id = payout_id

            # Debit from locked balance WITH ROW-LEVEL LOCK
            # This maintains the single-writer invariant
            wallet_result = await db_session.execute(
                select(Wallet)
                .where(Wallet.id == request.wallet_id)
                .with_for_update()  # Row-level lock
            )
            wallet = wallet_result.scalar_one_or_none()
            if wallet:
                wallet.locked_balance -= request.amount
                wallet.total_withdrawn += request.net_amount

            # Create transaction record
            tx = Transaction(
                wallet_id=request.wallet_id,
                tx_type="withdrawal",
                amount=request.amount,
                fee=request.fee,
                net_amount=request.net_amount,
                currency=request.currency,
                status="completed",
                reference_type="withdrawal_request",
                reference_id=request.id,
                description=f"Withdrawal to {request.destination_type}",
                blockchain_tx_hash=request.blockchain_tx_hash,
                completed_at=datetime.utcnow(),
            )
            db_session.add(tx)

            request.status = "completed"
            request.processed_at = datetime.utcnow()
            await db_session.commit()

        except Exception as e:
            request.status = "failed"
            request.rejection_reason = str(e)
            # Unlock the balance
            await wallet_manager.unlock_balance(
                str(request.wallet_id), request.amount, db_session
            )
            await db_session.commit()
            raise

        return request

    async def reject_withdrawal(
        self,
        request_id: str,
        reason: str,
        db_session: AsyncSession,
    ) -> WithdrawalRequest:
        """Reject a withdrawal request."""
        result = await db_session.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.id == request_id)
        )
        request = result.scalar_one_or_none()
        if not request:
            raise ValueError("Withdrawal request not found")

        if request.status not in ("pending", "approved"):
            raise ValueError("Request cannot be rejected")

        # Unlock the balance
        await wallet_manager.unlock_balance(
            str(request.wallet_id), request.amount, db_session
        )

        request.status = "rejected"
        request.rejection_reason = reason
        await db_session.commit()

        return request

    async def cancel_withdrawal(
        self,
        request_id: str,
        user_id: str,
        db_session: AsyncSession,
    ) -> WithdrawalRequest:
        """Cancel a pending withdrawal request."""
        result = await db_session.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.id == request_id)
        )
        request = result.scalar_one_or_none()
        if not request:
            raise ValueError("Withdrawal request not found")

        # Verify ownership
        wallet_result = await db_session.execute(
            select(Wallet).where(Wallet.id == request.wallet_id)
        )
        wallet = wallet_result.scalar_one_or_none()
        if not wallet or str(wallet.user_id) != user_id:
            raise ValueError("Not authorized")

        if request.status != "pending":
            raise ValueError("Only pending requests can be cancelled")

        # Unlock the balance
        await wallet_manager.unlock_balance(
            str(request.wallet_id), request.amount, db_session
        )

        request.status = "cancelled"
        await db_session.commit()

        return request

    async def _process_crypto_withdrawal(
        self,
        request: WithdrawalRequest,
        db_session: AsyncSession,
    ) -> str:
        """Process a crypto withdrawal."""
        # In production, this would:
        # 1. Connect to blockchain service
        # 2. Sign and broadcast transaction
        # 3. Return transaction hash

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.BLOCKCHAIN_SERVICE_URL}/blockchain/transfer",
                    json={
                        "to_address": request.destination_address,
                        "amount": str(request.net_amount),
                        "token": request.currency,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("tx_hash", "")
        except Exception:
            pass

        # Placeholder for development
        import secrets
        return f"0x{secrets.token_hex(32)}"

    async def _process_bank_withdrawal(
        self,
        request: WithdrawalRequest,
        db_session: AsyncSession,
    ) -> str:
        """Process a bank withdrawal via Stripe."""
        # In production, this would use Stripe Connect payouts
        # Placeholder for development
        import secrets
        return f"po_{secrets.token_hex(12)}"


payment_processor = PaymentProcessor()
withdrawal_processor = WithdrawalProcessor()

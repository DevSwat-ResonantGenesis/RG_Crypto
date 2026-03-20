"""Wallet management and operations."""

import hashlib
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Wallet, Transaction, Receipt, FundingSource
from .config import settings


class WalletManager:
    """Manages wallet operations."""

    async def create_wallet(
        self,
        user_id: str,
        db_session: AsyncSession,
    ) -> Wallet:
        """Create a new wallet for a user."""
        # Check if wallet exists
        result = await db_session.execute(
            select(Wallet).where(Wallet.user_id == user_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        wallet = Wallet(user_id=user_id)
        db_session.add(wallet)
        await db_session.commit()
        await db_session.refresh(wallet)
        return wallet

    async def get_wallet(
        self,
        user_id: str,
        db_session: AsyncSession,
    ) -> Optional[Wallet]:
        """Get wallet by user ID."""
        result = await db_session.execute(
            select(Wallet).where(Wallet.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_balance(
        self,
        wallet_id: str,
        db_session: AsyncSession,
    ) -> Dict[str, Decimal]:
        """Get wallet balances."""
        result = await db_session.execute(
            select(Wallet).where(Wallet.id == wallet_id)
        )
        wallet = result.scalar_one_or_none()
        if not wallet:
            return {}

        return {
            "available": wallet.token_balance,
            "pending": wallet.pending_balance,
            "locked": wallet.locked_balance,
            "total": wallet.token_balance + wallet.pending_balance + wallet.locked_balance,
        }

    async def credit(
        self,
        wallet_id: str,
        amount: Decimal,
        tx_type: str,
        description: str,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        db_session: AsyncSession = None,
    ) -> Transaction:
        """Credit tokens to a wallet."""
        # Use SELECT FOR UPDATE to maintain single-writer invariant
        result = await db_session.execute(
            select(Wallet)
            .where(Wallet.id == wallet_id)
            .with_for_update()  # Row-level lock
        )
        wallet = result.scalar_one_or_none()
        if not wallet:
            raise ValueError("Wallet not found")

        # Create transaction
        tx = Transaction(
            wallet_id=wallet_id,
            tx_type=tx_type,
            amount=amount,
            fee=Decimal("0"),
            net_amount=amount,
            currency=settings.PLATFORM_TOKEN_SYMBOL,
            status="completed",
            reference_type=reference_type,
            reference_id=reference_id,
            description=description,
            metadata=metadata,
            completed_at=datetime.utcnow(),
        )
        db_session.add(tx)

        # Update balance
        wallet.token_balance += amount
        if tx_type == "deposit":
            wallet.total_deposited += amount
        elif tx_type in ("reward", "sale"):
            wallet.total_earned += amount

        await db_session.commit()
        await db_session.refresh(tx)

        # Generate receipt
        await self._generate_receipt(tx, db_session)

        return tx

    async def debit(
        self,
        wallet_id: str,
        amount: Decimal,
        tx_type: str,
        description: str,
        fee: Decimal = Decimal("0"),
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        db_session: AsyncSession = None,
    ) -> Transaction:
        """Debit tokens from a wallet."""
        # Use SELECT FOR UPDATE to maintain single-writer invariant
        result = await db_session.execute(
            select(Wallet)
            .where(Wallet.id == wallet_id)
            .with_for_update()  # Row-level lock
        )
        wallet = result.scalar_one_or_none()
        if not wallet:
            raise ValueError("Wallet not found")

        total_debit = amount + fee
        if wallet.token_balance < total_debit:
            raise ValueError("Insufficient balance")

        # Create transaction
        tx = Transaction(
            wallet_id=wallet_id,
            tx_type=tx_type,
            amount=amount,
            fee=fee,
            net_amount=amount - fee,
            currency=settings.PLATFORM_TOKEN_SYMBOL,
            status="completed",
            reference_type=reference_type,
            reference_id=reference_id,
            description=description,
            metadata=metadata,
            completed_at=datetime.utcnow(),
        )
        db_session.add(tx)

        # Update balance
        wallet.token_balance -= total_debit
        if tx_type == "withdrawal":
            wallet.total_withdrawn += amount
        elif tx_type in ("purchase", "fee"):
            wallet.total_spent += total_debit

        await db_session.commit()
        await db_session.refresh(tx)

        # Generate receipt
        await self._generate_receipt(tx, db_session)

        return tx

    async def transfer(
        self,
        from_wallet_id: str,
        to_wallet_id: str,
        amount: Decimal,
        description: str,
        db_session: AsyncSession,
    ) -> Tuple[Transaction, Transaction]:
        """Transfer tokens between wallets."""
        # Debit from source
        debit_tx = await self.debit(
            wallet_id=from_wallet_id,
            amount=amount,
            tx_type="transfer",
            description=f"Transfer out: {description}",
            db_session=db_session,
        )
        debit_tx.counterparty_wallet_id = to_wallet_id

        # Credit to destination
        credit_tx = await self.credit(
            wallet_id=to_wallet_id,
            amount=amount,
            tx_type="transfer",
            description=f"Transfer in: {description}",
            db_session=db_session,
        )
        credit_tx.counterparty_wallet_id = from_wallet_id

        await db_session.commit()
        return debit_tx, credit_tx

    async def lock_balance(
        self,
        wallet_id: str,
        amount: Decimal,
        db_session: AsyncSession,
    ) -> bool:
        """Lock balance for pending withdrawal."""
        # Use SELECT FOR UPDATE to maintain single-writer invariant
        result = await db_session.execute(
            select(Wallet)
            .where(Wallet.id == wallet_id)
            .with_for_update()  # Row-level lock
        )
        wallet = result.scalar_one_or_none()
        if not wallet or wallet.token_balance < amount:
            return False

        wallet.token_balance -= amount
        wallet.locked_balance += amount
        await db_session.commit()
        return True

    async def unlock_balance(
        self,
        wallet_id: str,
        amount: Decimal,
        db_session: AsyncSession,
    ) -> bool:
        """Unlock previously locked balance."""
        # Use SELECT FOR UPDATE to maintain single-writer invariant
        result = await db_session.execute(
            select(Wallet)
            .where(Wallet.id == wallet_id)
            .with_for_update()  # Row-level lock
        )
        wallet = result.scalar_one_or_none()
        if not wallet or wallet.locked_balance < amount:
            return False

        wallet.locked_balance -= amount
        wallet.token_balance += amount
        await db_session.commit()
        return True

    async def _generate_receipt(
        self,
        tx: Transaction,
        db_session: AsyncSession,
    ) -> Receipt:
        """Generate a cryptographic receipt for a transaction."""
        receipt_number = f"RCP-{secrets.token_hex(8).upper()}"

        # Create receipt content for hashing
        content = {
            "receipt_number": receipt_number,
            "transaction_id": str(tx.id),
            "type": tx.tx_type,
            "amount": str(tx.amount),
            "currency": tx.currency,
            "timestamp": tx.created_at.isoformat(),
        }
        content_str = str(sorted(content.items()))
        content_hash = hashlib.sha256(content_str.encode()).hexdigest()

        receipt = Receipt(
            transaction_id=tx.id,
            receipt_number=receipt_number,
            receipt_type=tx.tx_type,
            payer_id=tx.wallet_id if tx.tx_type in ("purchase", "withdrawal", "transfer") else None,
            payee_id=tx.wallet_id if tx.tx_type in ("deposit", "reward", "sale") else None,
            amount=tx.amount,
            currency=tx.currency,
            content_hash=content_hash,
            metadata={"transaction_description": tx.description},
        )
        db_session.add(receipt)
        await db_session.commit()
        await db_session.refresh(receipt)
        return receipt


class FundingManager:
    """Manages funding sources."""

    async def add_card(
        self,
        wallet_id: str,
        stripe_payment_method_id: str,
        card_details: Dict[str, Any],
        db_session: AsyncSession,
    ) -> FundingSource:
        """Add a card as funding source."""
        source = FundingSource(
            wallet_id=wallet_id,
            source_type="card",
            stripe_payment_method_id=stripe_payment_method_id,
            card_last_four=card_details.get("last4"),
            card_brand=card_details.get("brand"),
            card_exp_month=card_details.get("exp_month"),
            card_exp_year=card_details.get("exp_year"),
            is_verified=True,
        )
        db_session.add(source)
        await db_session.commit()
        await db_session.refresh(source)
        return source

    async def add_crypto_wallet(
        self,
        wallet_id: str,
        address: str,
        chain: str,
        nickname: Optional[str] = None,
        db_session: AsyncSession = None,
    ) -> FundingSource:
        """Add a crypto wallet as funding source."""
        source = FundingSource(
            wallet_id=wallet_id,
            source_type="crypto_wallet",
            crypto_address=address,
            crypto_chain=chain,
            nickname=nickname,
            is_verified=False,  # Needs verification
        )
        db_session.add(source)
        await db_session.commit()
        await db_session.refresh(source)
        return source

    async def list_sources(
        self,
        wallet_id: str,
        db_session: AsyncSession,
    ) -> list:
        """List all funding sources for a wallet."""
        result = await db_session.execute(
            select(FundingSource)
            .where(FundingSource.wallet_id == wallet_id)
            .where(FundingSource.status == "active")
        )
        return list(result.scalars().all())

    async def remove_source(
        self,
        source_id: str,
        db_session: AsyncSession,
    ) -> bool:
        """Remove a funding source."""
        result = await db_session.execute(
            select(FundingSource).where(FundingSource.id == source_id)
        )
        source = result.scalar_one_or_none()
        if not source:
            return False

        source.status = "removed"
        await db_session.commit()
        return True

    async def set_default(
        self,
        wallet_id: str,
        source_id: str,
        db_session: AsyncSession,
    ) -> bool:
        """Set a funding source as default."""
        # Unset current default
        await db_session.execute(
            update(FundingSource)
            .where(FundingSource.wallet_id == wallet_id)
            .values(is_default=False)
        )

        # Set new default
        result = await db_session.execute(
            select(FundingSource).where(FundingSource.id == source_id)
        )
        source = result.scalar_one_or_none()
        if not source:
            return False

        source.is_default = True
        await db_session.commit()
        return True


wallet_manager = WalletManager()
funding_manager = FundingManager()

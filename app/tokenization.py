"""Token management and tokenization."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TokenAllocation, TokenPrice, Wallet, Transaction
from .config import settings


class TokenManager:
    """Manages platform token operations."""

    # Token economics
    TOTAL_SUPPLY = Decimal("1000000000")  # 1 billion tokens
    INITIAL_PRICE_USD = Decimal("0.01")  # $0.01 per token

    async def get_current_price(
        self,
        token: str = None,
        db_session: AsyncSession = None,
    ) -> Decimal:
        """Get current token price in USD."""
        token = token or settings.PLATFORM_TOKEN_SYMBOL
        
        result = await db_session.execute(
            select(TokenPrice)
            .where(TokenPrice.token == token)
            .order_by(TokenPrice.recorded_at.desc())
            .limit(1)
        )
        price_record = result.scalar_one_or_none()
        
        if price_record:
            return price_record.price_usd
        return self.INITIAL_PRICE_USD

    async def record_price(
        self,
        token: str,
        price_usd: Decimal,
        volume_24h: Optional[Decimal] = None,
        market_cap: Optional[Decimal] = None,
        change_24h: Optional[float] = None,
        source: str = "internal",
        db_session: AsyncSession = None,
    ) -> TokenPrice:
        """Record a new token price."""
        price_record = TokenPrice(
            token=token,
            price_usd=price_usd,
            volume_24h=volume_24h,
            market_cap=market_cap,
            change_24h=change_24h,
            source=source,
        )
        db_session.add(price_record)
        await db_session.commit()
        await db_session.refresh(price_record)
        return price_record

    async def usd_to_tokens(
        self,
        usd_amount: Decimal,
        db_session: AsyncSession,
    ) -> Decimal:
        """Convert USD to platform tokens."""
        price = await self.get_current_price(db_session=db_session)
        return usd_amount / price

    async def tokens_to_usd(
        self,
        token_amount: Decimal,
        db_session: AsyncSession,
    ) -> Decimal:
        """Convert platform tokens to USD."""
        price = await self.get_current_price(db_session=db_session)
        return token_amount * price

    async def create_allocation(
        self,
        wallet_id: str,
        allocation_type: str,
        total_amount: Decimal,
        vesting_start: Optional[datetime] = None,
        vesting_end: Optional[datetime] = None,
        cliff_date: Optional[datetime] = None,
        vesting_interval: Optional[str] = None,
        description: Optional[str] = None,
        db_session: AsyncSession = None,
    ) -> TokenAllocation:
        """Create a token allocation with optional vesting."""
        allocation = TokenAllocation(
            wallet_id=wallet_id,
            allocation_type=allocation_type,
            total_amount=total_amount,
            vesting_start=vesting_start,
            vesting_end=vesting_end,
            cliff_date=cliff_date,
            vesting_interval=vesting_interval,
            description=description,
        )
        db_session.add(allocation)
        await db_session.commit()
        await db_session.refresh(allocation)
        return allocation

    async def get_vested_amount(
        self,
        allocation: TokenAllocation,
    ) -> Decimal:
        """Calculate vested amount for an allocation."""
        now = datetime.utcnow()

        # If no vesting, all tokens are available
        if not allocation.vesting_start or not allocation.vesting_end:
            return allocation.total_amount

        # Check cliff
        if allocation.cliff_date and now < allocation.cliff_date:
            return Decimal("0")

        # Check if fully vested
        if now >= allocation.vesting_end:
            return allocation.total_amount

        # Calculate linear vesting
        total_duration = (allocation.vesting_end - allocation.vesting_start).total_seconds()
        elapsed = (now - allocation.vesting_start).total_seconds()
        
        if total_duration <= 0:
            return allocation.total_amount

        vested_ratio = Decimal(str(elapsed / total_duration))
        return allocation.total_amount * vested_ratio

    async def release_vested_tokens(
        self,
        allocation_id: str,
        db_session: AsyncSession,
    ) -> Decimal:
        """Release vested tokens to wallet."""
        from .wallet import wallet_manager

        result = await db_session.execute(
            select(TokenAllocation).where(TokenAllocation.id == allocation_id)
        )
        allocation = result.scalar_one_or_none()
        if not allocation:
            return Decimal("0")

        vested = await self.get_vested_amount(allocation)
        releasable = vested - allocation.released_amount

        if releasable <= 0:
            return Decimal("0")

        # Credit tokens to wallet
        await wallet_manager.credit(
            wallet_id=str(allocation.wallet_id),
            amount=releasable,
            tx_type="reward",
            description=f"Vested tokens from {allocation.allocation_type}",
            reference_type="allocation",
            reference_id=str(allocation.id),
            db_session=db_session,
        )

        allocation.released_amount += releasable
        if allocation.released_amount >= allocation.total_amount:
            allocation.status = "completed"

        await db_session.commit()
        return releasable

    async def get_allocations(
        self,
        wallet_id: str,
        db_session: AsyncSession,
    ) -> List[TokenAllocation]:
        """Get all allocations for a wallet."""
        result = await db_session.execute(
            select(TokenAllocation)
            .where(TokenAllocation.wallet_id == wallet_id)
            .order_by(TokenAllocation.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_token_stats(
        self,
        db_session: AsyncSession,
    ) -> Dict[str, Any]:
        """Get overall token statistics."""
        # Total circulating supply (sum of all wallet balances)
        result = await db_session.execute(
            select(func.sum(Wallet.token_balance))
        )
        circulating = result.scalar() or Decimal("0")

        # Total locked
        result = await db_session.execute(
            select(func.sum(Wallet.locked_balance))
        )
        locked = result.scalar() or Decimal("0")

        # Total pending allocations
        result = await db_session.execute(
            select(func.sum(TokenAllocation.total_amount - TokenAllocation.released_amount))
            .where(TokenAllocation.status == "active")
        )
        pending_allocations = result.scalar() or Decimal("0")

        # Current price
        price = await self.get_current_price(db_session=db_session)

        return {
            "total_supply": str(self.TOTAL_SUPPLY),
            "circulating_supply": str(circulating),
            "locked_supply": str(locked),
            "pending_allocations": str(pending_allocations),
            "current_price_usd": str(price),
            "market_cap_usd": str(circulating * price),
        }


class RewardEngine:
    """Manages token rewards and incentives."""

    # Reward rates
    AGENT_SALE_REWARD_PERCENT = Decimal("85")  # Publisher gets 85%
    PLATFORM_FEE_PERCENT = Decimal("15")  # Platform takes 15%
    REFERRAL_REWARD_PERCENT = Decimal("5")  # Referrer gets 5%

    async def reward_agent_sale(
        self,
        publisher_wallet_id: str,
        sale_amount: Decimal,
        referrer_wallet_id: Optional[str] = None,
        db_session: AsyncSession = None,
    ) -> Dict[str, Decimal]:
        """Distribute rewards for an agent sale."""
        from .wallet import wallet_manager

        rewards = {}

        # Publisher reward
        publisher_reward = sale_amount * (self.AGENT_SALE_REWARD_PERCENT / 100)
        await wallet_manager.credit(
            wallet_id=publisher_wallet_id,
            amount=publisher_reward,
            tx_type="sale",
            description="Agent sale revenue",
            db_session=db_session,
        )
        rewards["publisher"] = publisher_reward

        # Referral reward
        if referrer_wallet_id:
            referral_reward = sale_amount * (self.REFERRAL_REWARD_PERCENT / 100)
            await wallet_manager.credit(
                wallet_id=referrer_wallet_id,
                amount=referral_reward,
                tx_type="reward",
                description="Referral reward",
                db_session=db_session,
            )
            rewards["referrer"] = referral_reward

        return rewards

    async def reward_usage(
        self,
        wallet_id: str,
        usage_type: str,
        amount: Decimal,
        db_session: AsyncSession,
    ) -> Transaction:
        """Reward tokens for platform usage."""
        from .wallet import wallet_manager

        return await wallet_manager.credit(
            wallet_id=wallet_id,
            amount=amount,
            tx_type="reward",
            description=f"Usage reward: {usage_type}",
            db_session=db_session,
        )


token_manager = TokenManager()
reward_engine = RewardEngine()

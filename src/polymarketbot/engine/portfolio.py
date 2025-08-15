"""Portfolio tracking and mark-to-market PnL."""

from __future__ import annotations

from datetime import date

from polymarketbot.models import Fill, MarketState, PortfolioSnapshot, Position, Side, utcnow

class Portfolio:
    def __init__(self, starting_cash: float = 10_000.0):
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.peak_equity = starting_cash
        self._day = date.today()
        self._day_start_equity = starting_cash
        self.token_to_market: dict[str, str] = {}

    def register_market_token(self, token_id: str, condition_id: str) -> None:
        self.token_to_market[token_id] = condition_id

    def apply_fill(self, fill: Fill) -> None:
        self._roll_day_if_needed(None)
        pos = self.positions.get(fill.token_id) or Position(
            token_id=fill.token_id,
            market_condition_id=fill.market_condition_id
            or self.token_to_market.get(fill.token_id, ""),
        )
        signed = fill.size if fill.side == Side.BUY else -fill.size
        notional = fill.price * fill.size

        if fill.side == Side.BUY:
            self.cash -= notional + fill.fee
            new_size = pos.size + signed
            if pos.size >= 0 and new_size >= 0:
                # adding to long / opening long
                total = pos.size * pos.avg_price + notional
                pos.avg_price = (total / new_size) if new_size else 0.0
            elif pos.size < 0:
                # covering short
                closed = min(abs(pos.size), fill.size)
                pos.realized_pnl += (pos.avg_price - fill.price) * closed
                self.realized_pnl += (pos.avg_price - fill.price) * closed
                if new_size > 0:
                    pos.avg_price = fill.price
            pos.size = new_size
        else:
            self.cash += notional - fill.fee
            new_size = pos.size + signed
            if pos.size <= 0 and new_size <= 0:
                total = abs(pos.size) * pos.avg_price + notional
                pos.avg_price = (total / abs(new_size)) if new_size else 0.0
            elif pos.size > 0:
                closed = min(pos.size, fill.size)
                pos.realized_pnl += (fill.price - pos.avg_price) * closed
                self.realized_pnl += (fill.price - pos.avg_price) * closed
                if new_size < 0:
                    pos.avg_price = fill.price
            pos.size = new_size

        if abs(pos.size) < 1e-12:
            pos.size = 0.0
            pos.avg_price = 0.0
            self.positions.pop(fill.token_id, None)
        else:
            self.positions[fill.token_id] = pos

    def position_size(self, token_id: str) -> float:
        pos = self.positions.get(token_id)
        return pos.size if pos else 0.0

    def market_exposure(self, condition_id: str) -> float:
        total = 0.0
        for pos in self.positions.values():
            if pos.market_condition_id == condition_id:
                total += abs(pos.size) * pos.avg_price
        return total
    def open_market_count(self) -> int:
        markets = {
            p.market_condition_id
            for p in self.positions.values()
            if abs(p.size) > 0 and p.market_condition_id
        }
        return len(markets)

    def total_exposure(self) -> float:
        return sum(abs(p.size) * p.avg_price for p in self.positions.values())

    def mark_snapshot(self, states: dict[str, MarketState] | None = None) -> PortfolioSnapshot:
        marks = self._mark_prices(states or {})
        unrealized = 0.0
        for token_id, pos in self.positions.items():
            mark = marks.get(token_id, pos.avg_price)
            unrealized += (mark - pos.avg_price) * pos.size
        equity = self.cash + sum(
            self.positions[t].size * marks.get(t, self.positions[t].avg_price)
            for t in self.positions
        )
        self._roll_day_if_needed(equity)
        self.peak_equity = max(self.peak_equity, equity)
        daily_pnl = equity - self._day_start_equity
        snap = PortfolioSnapshot(
            cash=self.cash,
            equity=equity,
            unrealized_pnl=unrealized,
            realized_pnl=self.realized_pnl,
            daily_pnl=daily_pnl,
            peak_equity=self.peak_equity,
            exposure=self.total_exposure(),
            open_markets=self.open_market_count(),
            positions=dict(self.positions),
            updated_at=utcnow(),
        )
        return snap

    def _mark_prices(self, states: dict[str, MarketState]) -> dict[str, float]:
        marks: dict[str, float] = {}
        for state in states.values():
            if state.yes_book and state.yes_book.mid is not None:
                marks[state.market.yes_token_id] = state.yes_book.mid
            if state.no_book and state.no_book.mid is not None:
                marks[state.market.no_token_id] = state.no_book.mid
        return marks

    def _roll_day_if_needed(self, equity: float | None) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            if equity is None:
                equity = self.cash + sum(p.size * p.avg_price for p in self.positions.values())
            self._day_start_equity = equity


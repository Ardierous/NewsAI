import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

import requests
from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger("app.cost")

try:
    from zoneinfo import ZoneInfo

    MSK_TZ = ZoneInfo("Europe/Moscow")
except Exception:
    MSK_TZ = None  # type: ignore[misc, assignment]


@dataclass
class BalanceSnapshot:
    balance: float | None
    budget_limit: float | None = None
    budget_used: float | None = None


@dataclass
class CostMeasurement:
    cost_rub: float | None
    balance_before: float | None
    balance_after: float | None
    source: str


class ProxyApiCostTracker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.proxyapi_base_url.rstrip("/")
        self.api_key = self.settings.proxyapi_api_key
        self._measure_lock = threading.Lock()

    def get_balance_snapshot(self) -> BalanceSnapshot:
        endpoints = [
            "https://api.proxyapi.ru/proxyapi/balance",
            "https://api.proxyapi.ru/balance",
            f"{self.base_url}/proxyapi/balance",
        ]
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for url in endpoints:
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code != 200:
                    continue
                data = response.json()
                value = data.get("balance") or data.get("amount") or data.get("value") or data.get("rub")
                if value is None:
                    continue
                budget = data.get("budget") if isinstance(data.get("budget"), dict) else {}
                return BalanceSnapshot(
                    balance=float(value),
                    budget_limit=float(budget["limit"]) if budget.get("limit") is not None else None,
                    budget_used=float(budget["used"]) if budget.get("used") is not None else None,
                )
            except Exception:
                continue
        logger.warning("Не удалось получить баланс ProxyAPI. Проверьте разрешение 'Запрос баланса' у API-ключа.")
        return BalanceSnapshot(balance=None)

    def get_balance(self) -> float | None:
        return self.get_balance_snapshot().balance

    def measure(self, fn: Callable[[], object], source: str, **kwargs: object) -> tuple[object, CostMeasurement]:
        """Выполняет вызов без опроса баланса; стоимость считается снимками до/после шага дайджеста."""
        del kwargs
        with self._measure_lock:
            result = fn()
            return result, CostMeasurement(
                cost_rub=None,
                balance_before=None,
                balance_after=None,
                source=source,
            )


def _msk_today() -> date:
    if MSK_TZ is not None:
        return datetime.now(MSK_TZ).date()
    return datetime.utcnow().date()


def record_today_balance(db: Session, snap: BalanceSnapshot) -> None:
    """Первый снимок за сутки (МСК) — opening; каждый вызов обновляет last."""
    from app.models import ProxyapiSpendDay

    if snap.balance is None and snap.budget_used is None:
        return
    today = _msk_today()
    row = db.query(ProxyapiSpendDay).filter(ProxyapiSpendDay.day == today).first()
    if row is None:
        yesterday = today - timedelta(days=1)
        prev = db.query(ProxyapiSpendDay).filter(ProxyapiSpendDay.day == yesterday).first()
        opening_balance = prev.last_balance if prev and prev.last_balance is not None else snap.balance
        opening_budget_used = (
            prev.last_budget_used if prev and prev.last_budget_used is not None else snap.budget_used
        )
        row = ProxyapiSpendDay(
            day=today,
            opening_balance=opening_balance,
            last_balance=snap.balance,
            opening_budget_used=opening_budget_used,
            last_budget_used=snap.budget_used,
        )
        db.add(row)
    else:
        if snap.balance is not None:
            row.last_balance = snap.balance
        if snap.budget_used is not None:
            row.last_budget_used = snap.budget_used
    db.commit()


def proxyapi_spent_today_rub(db: Session, tracker: ProxyApiCostTracker) -> float | None:
    """Траты за календарный день (МСК): opening − last по бюджету ключа или балансу аккаунта."""
    from app.models import ProxyapiSpendDay

    snap = tracker.get_balance_snapshot()
    record_today_balance(db, snap)
    row = db.query(ProxyapiSpendDay).filter(ProxyapiSpendDay.day == _msk_today()).first()
    if row is None:
        return None
    if row.opening_budget_used is not None and row.last_budget_used is not None:
        return round(max(0.0, row.last_budget_used - row.opening_budget_used), 4)
    if row.opening_balance is not None and row.last_balance is not None:
        return round(max(0.0, row.opening_balance - row.last_balance), 4)
    return None


# Совместимость со старым именем (lifespan main.py)
touch_proxyapi_spend_day = record_today_balance

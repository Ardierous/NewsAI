import logging
import time
from dataclasses import dataclass
from typing import Callable

import requests

from app.config import get_settings

logger = logging.getLogger("app.cost")


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

    def get_balance(self) -> float | None:
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
                if value is not None:
                    return float(value)
            except Exception:
                continue
        logger.warning("Не удалось получить баланс ProxyAPI. Проверьте разрешение 'Запрос баланса' у API-ключа.")
        return None

    def measure(self, fn: Callable[[], object], source: str, sleep_seconds: float = 1.5) -> tuple[object, CostMeasurement]:
        before = self.get_balance()
        result = fn()
        if before is None:
            return result, CostMeasurement(cost_rub=None, balance_before=None, balance_after=None, source=source)
        time.sleep(sleep_seconds)
        after = self.get_balance()
        if after is None:
            return result, CostMeasurement(cost_rub=None, balance_before=before, balance_after=None, source=source)
        cost = max(0.0, before - after)
        return result, CostMeasurement(cost_rub=round(cost, 6), balance_before=before, balance_after=after, source=source)

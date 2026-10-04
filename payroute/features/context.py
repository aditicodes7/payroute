"""Turning a logged context into a feature vector.

The one rule this module exists to enforce: its input is a plain mapping — the
dict `context_of()` produces — and never a Transaction or a Simulator. Handing
it the Transaction would put the whole simulator one attribute access away and
the oracle two. With a mapping, leaking requires adding a field to
`context_of()`, which is a visible, reviewable line rather than an accident.

R2 deliberately has no gateway-health feature. R4's entire measurement is the
delta from adding time-decayed health signals; if R2 already had one, that
delta would be a confound rather than a result.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np

from payroute.sim.config import SimulatorConfig


class Featuriser:
    def __init__(
        self,
        methods: Sequence[str],
        issuers: Sequence[str],
        categories: Sequence[str],
        amount_log_mean: float,
        amount_log_std: float,
        health_gateways: Sequence[str] = (),
    ):
        self.methods = list(methods)
        self.issuers = list(issuers)
        self.categories = list(categories)
        self.amount_log_mean = amount_log_mean
        self.amount_log_std = amount_log_std
        # Empty by default: R2 is static context only, so that R4's addition of
        # health signals is a measurable delta rather than a confound.
        self.health_gateways = list(health_gateways)

        self._method_index = {name: i for i, name in enumerate(self.methods)}
        self._issuer_index = {name: i for i, name in enumerate(self.issuers)}
        self._category_index = {name: i for i, name in enumerate(self.categories)}

        self.names = (
            [f"method={m}" for m in self.methods]
            + [f"issuer={i}" for i in self.issuers]
            + [f"category={c}" for c in self.categories]
            + ["log_amount_z", "hour_sin", "hour_cos", "bias"]
            + [f"health={g}" for g in self.health_gateways]
        )
        self.dim = len(self.names)

        self._amount_at = len(self.methods) + len(self.issuers) + len(self.categories)

    @classmethod
    def from_config(cls, config: SimulatorConfig, health_gateways: Sequence[str] = ()) -> "Featuriser":
        """Derive the amount scaling analytically from the configured mixture.

        Unscaled, log-amount ranges over roughly +/-8 while every one-hot is 0
        or 1 — it would dominate both the ridge penalty and the confidence
        width x^T A^-1 x, and the exploration bonus would stop meaning anything.
        """
        weights = config.method_mix
        medians = config.amount.median_paise_by_method
        sigmas = config.amount.log_sigma_by_method

        mean = sum(w * math.log(medians[m]) for m, w in weights.items())
        variance = sum(
            w * (sigmas[m] ** 2 + (math.log(medians[m]) - mean) ** 2) for m, w in weights.items()
        )
        return cls(
            methods=list(config.method_mix),
            issuers=list(config.issuer_mix),
            categories=list(config.merchant_category_mix),
            amount_log_mean=mean,
            amount_log_std=math.sqrt(variance),
            health_gateways=health_gateways,
        )

    def transform(self, context: Mapping) -> np.ndarray:
        if not isinstance(context, Mapping):
            raise TypeError(
                f"the featuriser takes the logged context mapping, not {type(context).__name__}. "
                "Passing the Transaction would put the simulator one attribute access away."
            )

        x = np.zeros(self.dim)
        x[self._method_index[context["method"]]] = 1.0
        x[len(self.methods) + self._issuer_index[context["issuer"]]] = 1.0
        x[len(self.methods) + len(self.issuers) + self._category_index[context["merchant_category"]]] = 1.0

        i = self._amount_at
        x[i] = (math.log(context["amount_paise"]) - self.amount_log_mean) / self.amount_log_std

        # Cyclic, because 23:00 and 00:00 are an hour apart. A raw integer hour
        # would place the busiest and quietest parts of the night at opposite
        # ends of the feature space.
        angle = 2.0 * math.pi * context["hour_of_day"] / 24.0
        x[i + 1] = math.sin(angle)
        x[i + 2] = math.cos(angle)
        x[i + 3] = 1.0

        for offset, gateway in enumerate(self.health_gateways, start=i + 4):
            x[offset] = context[f"health_{gateway}"]
        return x

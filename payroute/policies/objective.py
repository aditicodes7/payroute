"""The money.

    E[net | x, g] = p(x, g) * ( value(x) - fee(x, g) )

The fee is INSIDE the probability-weighted term because R0 decided it is
charged on success only: on a failure we earn nothing and pay nothing. The
other arrangement, `p*value - fee`, differs by `(1-p)*fee` — about 5% of margin
at p = 0.9, biased consistently toward reliable gateways.

Nothing here is learned. R2 established that the probability is the learnable
part and the money is arithmetic; regressing on revenue would put a heavy-tailed
quantity where UCB expects a bounded one.
"""

from __future__ import annotations


def transaction_value(amount_paise: int, take_rate_bps: int) -> int:
    return (take_rate_bps * amount_paise) // 10_000


def gateway_fee(amount_paise: int, flat_paise: int, bps: int) -> int:
    return flat_paise + (bps * amount_paise) // 10_000


def expected_net_revenue(
    amount_paise: int, take_rate_bps: int, flat_paise: int, bps: int, p_success: float
) -> float:
    value = transaction_value(amount_paise, take_rate_bps)
    fee = gateway_fee(amount_paise, flat_paise, bps)
    return p_success * (value - fee)

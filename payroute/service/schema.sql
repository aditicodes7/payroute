-- The decision log. Postgres is a projection of the Kafka decisions topic,
-- not the system of record, but it is what R3 runs SQL over.
--
-- The NOT NULL on propensity is the project's invariant stated at the lowest
-- layer that can enforce it. The Python dataclass already refuses to build a
-- record without one, which covers every path through our code; this covers
-- the paths that do not -- a backfill script, a manual insert, a future
-- service written by someone who never read CLAUDE.md.

CREATE TABLE IF NOT EXISTS decisions (
    txn_id            TEXT        PRIMARY KEY,
    ts                TIMESTAMPTZ NOT NULL,
    policy_id         TEXT        NOT NULL,
    policy_version    TEXT        NOT NULL,

    context           JSONB       NOT NULL,
    action            TEXT        NOT NULL,
    propensity        DOUBLE PRECISION NOT NULL CHECK (propensity > 0 AND propensity <= 1),
    action_probs      JSONB       NOT NULL,

    -- R5: whether the latency constraint was met, so a violation can never be
    -- silently relaxed. R6: whether health came from the store or from priors,
    -- so decisions made blind are not pooled with informed ones.
    constraint_status TEXT,
    predicted_p95_ms  DOUBLE PRECISION,
    health_source     TEXT,

    -- Outcomes arrive separately, over the feedback topic.
    success           BOOLEAN,
    latency_ms        DOUBLE PRECISION,
    fee_paise         BIGINT,
    net_revenue_paise BIGINT,
    outcome_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS decisions_ts_idx ON decisions (ts);
CREATE INDEX IF NOT EXISTS decisions_policy_idx ON decisions (policy_id, ts);

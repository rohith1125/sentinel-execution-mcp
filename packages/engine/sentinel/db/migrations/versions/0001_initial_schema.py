"""Initial schema — all Sentinel tables.

Revision ID: 0001
Revises:
Create Date: 2026-03-19 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # watchlist_entries
    op.create_table(
        "watchlist_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("asset_class", sa.String(20), nullable=False, server_default="equity"),
        sa.Column("group_tags", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol"),
    )
    op.create_index("ix_watchlist_entries_symbol", "watchlist_entries", ["symbol"])

    # strategy_records
    op.create_table(
        "strategy_records",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("config", postgresql.JSONB(), nullable=True),
        sa.Column("performance_metrics", postgresql.JSONB(), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_by", sa.String(128), nullable=True),
        sa.Column("demotion_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_strategy_records_state", "strategy_records", ["state"])

    # orders
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("client_order_id", sa.String(64), nullable=False),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 8), nullable=False),
        sa.Column("limit_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("stop_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("time_in_force", sa.String(10), nullable=False, server_default="day"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("filled_qty", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("filled_avg_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False, server_default="paper"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_order_id"),
    )
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_strategy_id", "orders", ["strategy_id"])
    op.create_index("ix_orders_account_id", "orders", ["account_id"])
    op.create_index("ix_orders_broker_order_id", "orders", ["broker_order_id"])

    # positions
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("side", sa.String(10), nullable=False, server_default="long"),
        sa.Column("quantity", sa.Numeric(18, 8), nullable=False),
        sa.Column("avg_entry_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("current_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False, server_default="paper"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_positions_symbol", "positions", ["symbol"])
    op.create_index("ix_positions_strategy_id", "positions", ["strategy_id"])
    op.create_index("ix_positions_account_id", "positions", ["account_id"])

    # audit_events
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("regime_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("signal_details", postgresql.JSONB(), nullable=True),
        sa.Column("risk_check_results", postgresql.JSONB(), nullable=True),
        sa.Column("decision_outcome", sa.String(40), nullable=True),
        sa.Column("decision_explanation", sa.Text(), nullable=True),
        sa.Column("sizing_details", postgresql.JSONB(), nullable=True),
        sa.Column("execution_details", postgresql.JSONB(), nullable=True),
        sa.Column("outcome", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_symbol", "audit_events", ["symbol"])
    op.create_index("ix_audit_events_strategy_id", "audit_events", ["strategy_id"])
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])

    # trade_journals
    op.create_table(
        "trade_journals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("orders.id"),
            nullable=False,
        ),
        sa.Column("entry_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("exit_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(18, 8), nullable=True),
        sa.Column("pnl_pct", sa.Numeric(10, 6), nullable=True),
        sa.Column("mae", sa.Numeric(18, 8), nullable=True),
        sa.Column("mfe", sa.Numeric(18, 8), nullable=True),
        sa.Column("holding_period_seconds", sa.Integer(), nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("regime_at_entry", sa.String(40), nullable=True),
        sa.Column("exit_reason", sa.String(128), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_journals_order_id", "trade_journals", ["order_id"])
    op.create_index("ix_trade_journals_symbol", "trade_journals", ["symbol"])
    op.create_index("ix_trade_journals_strategy_id", "trade_journals", ["strategy_id"])

    # strategy_promotions
    op.create_table(
        "strategy_promotions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "strategy_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("strategy_records.id"),
            nullable=False,
        ),
        sa.Column("from_state", sa.String(30), nullable=False),
        sa.Column("to_state", sa.String(30), nullable=False),
        sa.Column("criteria_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("metrics_at_promotion", postgresql.JSONB(), nullable=True),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_promotions_strategy_id", "strategy_promotions", ["strategy_id"])


def downgrade() -> None:
    op.drop_table("strategy_promotions")
    op.drop_table("trade_journals")
    op.drop_table("audit_events")
    op.drop_table("positions")
    op.drop_table("orders")
    op.drop_table("strategy_records")
    op.drop_table("watchlist_entries")

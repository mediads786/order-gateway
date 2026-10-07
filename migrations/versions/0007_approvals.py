"""Add workflow approvals."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_approvals"
down_revision = "0006_workflow_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("requested_by_key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_by_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_by_name", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.String(length=200), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXECUTED', 'EXECUTION_FAILED')",
            name="ck_approvals_status",
        ),
        sa.CheckConstraint(
            "decided_by_key_id IS NULL OR decided_by_key_id <> requested_by_key_id",
            name="ck_approvals_no_self_decision",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"]),
        sa.ForeignKeyConstraint(["requested_by_key_id"], ["api_keys.key_id"]),
        sa.ForeignKeyConstraint(["decided_by_key_id"], ["api_keys.key_id"]),
        sa.PrimaryKeyConstraint("approval_id"),
        sa.UniqueConstraint("order_id", name="uq_approvals_order_id"),
    )
    op.create_index("ix_approvals_status_created_at", "approvals", ["status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_approvals_status_created_at", table_name="approvals")
    op.drop_table("approvals")

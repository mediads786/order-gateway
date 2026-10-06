"""Widen order status and retain the initial response status."""

from alembic import op
import sqlalchemy as sa

revision = "0002_status_and_replay_code"
down_revision = "0001_intake_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("orders", "status", existing_type=sa.String(length=10), type_=sa.String(length=32), existing_nullable=False)
    op.add_column("idempotency_keys", sa.Column("status_code", sa.Integer(), server_default="201", nullable=False))
    op.alter_column("idempotency_keys", "status_code", server_default=None)


def downgrade() -> None:
    op.drop_column("idempotency_keys", "status_code")
    op.alter_column("orders", "status", existing_type=sa.String(length=32), type_=sa.String(length=10), existing_nullable=False)

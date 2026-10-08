"""Prevent concurrent open cancellation approvals for one order."""

from alembic import op

revision = "0009_open_cancel_approval"
down_revision = "0008_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_approvals_open_cancel ON approvals ((input->>'order_id')) "
        "WHERE workflow = 'cancel_order' AND status IN ('PENDING', 'APPROVED')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_approvals_open_cancel")

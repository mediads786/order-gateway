"""Add queue jobs and ERP order identifiers."""

from alembic import op
import sqlalchemy as sa

revision = "0003_jobs_and_erp_order_id"
down_revision = "0002_status_and_replay_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("erp_order_id", sa.String(), nullable=True))
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"]),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("order_id"),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_column("orders", "erp_order_id")

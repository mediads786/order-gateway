"""Add retry scheduling to jobs."""

from alembic import op
import sqlalchemy as sa

revision = "0004_job_retries"
down_revision = "0003_jobs_and_erp_order_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_jobs_status_next_attempt_at", "jobs", ["status", "next_attempt_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_status_next_attempt_at", table_name="jobs")
    op.drop_column("jobs", "next_attempt_at")

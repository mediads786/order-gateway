"""Add natural-language proposals."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_proposals"
down_revision = "0007_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.Text(), nullable=False),
        sa.Column("proposer", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("workflow", sa.Text(), nullable=True),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("explanation", sa.String(length=300), nullable=True),
        sa.Column("invalid_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "status IN ('PROPOSED', 'INVALID', 'CONFIRMED', 'DISCARDED')",
            name="ck_proposals_status",
        ),
        sa.ForeignKeyConstraint(["requested_by_key_id"], ["api_keys.key_id"]),
        sa.PrimaryKeyConstraint("proposal_id"),
    )
    op.create_index(
        "ix_proposals_requested_by_created_at", "proposals", ["requested_by_key_id", "created_at"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_proposals_requested_by_created_at", table_name="proposals")
    op.drop_table("proposals")

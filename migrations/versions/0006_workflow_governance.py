"""Add API keys and append-only workflow events."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_workflow_governance"
down_revision = "0005_shipments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('operator', 'approver', 'admin')", name="ck_api_keys_role"),
        sa.PrimaryKeyConstraint("key_id"),
        sa.UniqueConstraint("name", name="uq_api_keys_name"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_table(
        "workflow_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_name", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("workflow", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_hash", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["key_id"], ["api_keys.key_id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_workflow_events_request_id", "workflow_events", ["request_id"], unique=False)
    op.execute(
        """CREATE FUNCTION reject_workflow_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'workflow_events is append-only';
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER workflow_events_append_only
        BEFORE UPDATE OR DELETE ON workflow_events
        FOR EACH ROW EXECUTE FUNCTION reject_workflow_event_mutation()"""
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS workflow_events_append_only ON workflow_events")
    op.execute("DROP FUNCTION IF EXISTS reject_workflow_event_mutation()")
    op.drop_index("ix_workflow_events_request_id", table_name="workflow_events")
    op.drop_table("workflow_events")
    op.drop_table("api_keys")

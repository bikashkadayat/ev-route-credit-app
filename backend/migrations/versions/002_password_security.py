"""Password reset tokens and password history.

Revision ID: 0002
Revises: 0001

Two `[M]` requirements cannot be met without storage that Doc 05 does not define:

* **FR-1.6** — "Password reset by single-use, 30-minute, emailed token." A token that is
  not stored cannot be single-use, and one stored in plaintext is a password equivalent
  sitting in the database. So: the SHA-256 hash only, with an explicit `used_at` so a
  redeemed token can never be replayed.
* **FR-1.2** — "last 5 reused passwords rejected." Comparing against previous passwords
  requires keeping their hashes.

Both tables follow the conventions the rest of the schema uses (Doc 05 §5.1): BIGSERIAL
primary key, timezone-aware timestamps, cascade from the owning user, and an index on every
column the application actually filters by.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The hash only. A stored plaintext reset token is a password equivalent.
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Set the moment it is redeemed, which is what makes it single-use.
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("requested_ip", sa.dialects.postgresql.INET()),
        sa.UniqueConstraint("token_hash", name="uq_reset_token"),
    )
    op.create_index(
        "idx_reset_user", "password_reset_tokens", ["user_id", "expires_at"]
    )

    op.create_table(
        "password_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_pwhistory_user", "password_history", ["user_id", "changed_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_pwhistory_user", table_name="password_history")
    op.drop_table("password_history")
    op.drop_index("idx_reset_user", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")

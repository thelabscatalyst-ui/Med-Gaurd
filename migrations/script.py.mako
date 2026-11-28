"""${message}

Revision: ${up_revision}  ·  Revises: ${down_revision | comma,n}
Review with docs/MIGRATIONS.md §6 before merging.
"""

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    op.execute("""
    """)


def downgrade() -> None:
    op.execute("""
    """)

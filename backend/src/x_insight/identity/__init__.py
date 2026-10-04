"""Identity domain: singleton admin seeding, auth sessions, own credentials.

S03 (plan.md §§2.1, 4.3, 11): exactly one ``admin`` seeded once with password
``admin`` (hash only), opaque hashed sessions, credential-revision revocation,
no timeout/complexity. Privileges come from the stored account, never the
login-selected role.
"""

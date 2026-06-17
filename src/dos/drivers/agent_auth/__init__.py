"""agent_auth — per-agent ``AccountAuthSpec`` drivers (the vendor-named tier).

Each module here exposes a ``SPEC = AccountAuthSpec(...)`` that names ONE agent's
auth literals (its config-dir env var, token env, creds/token file names, token
prefix, enroll flow). These are the vendor names the kernel forbids in its own
modules (``tests/test_vendor_agnostic_kernel.py``) — so they live in ``drivers/``
(the home for provider/vendor names) and are resolved by ``agent_kind`` through
``dos.account_auth.resolve_account_auth`` (in-tree first, then the
``dos.account_auth`` entry-point group). Adding an agent = a new module here (or a
third-party ``dos.account_auth`` plugin), never a kernel edit.

The seat picker itself (``account_switcher.pick_account`` / ``serving_pool`` /
``allocate_seats``) stays agent-blind; only this auth glue varies per agent.
"""

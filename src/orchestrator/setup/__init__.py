"""``orchestrator setup``: put Spine into every installed client in one command (SSPN-340).

The pieces, in the order a run uses them:

- :mod:`.entry` works out the one server entry every client should carry: the ``uvx`` line,
  the level and the path to the user's keys file.
- :mod:`.clients` holds one adapter per client. Each knows where its client keeps the
  entry, how to read it, and how to write it without disturbing anything else in that file.
- :mod:`.plan` compares each client's entry with the wanted one and says what would change
  (``add``, ``same``, ``replace``), or why a client is skipped.
- :mod:`.apply` backs a file up, makes the change, and records what it did, so a later
  ``--undo`` removes exactly that and nothing else.

Two rules hold everywhere, and the guard tests in ``tests/setup/test_guards.py`` enforce
them. **Nothing is written outside the user's home directory**, and **no value read from a
client's configuration or output is ever printed**. Env values are shown as their names
only, because a client's config holds other servers' tokens in plain text (Codex does, and
``codex mcp list --json`` prints them).

This module imports nothing, so importing a submodule never runs the others.
"""

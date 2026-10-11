"""SWE-bench Verified harness: run Spine's pipeline on benchmark instances and keep the books.

The grader is the official one (``swebench``, run separately and pinned); nothing here scores a
patch. What lives here is the part only Spine can do: build a generation environment from an
instance without letting the answer into it, run the pipeline as its own subprocess under a clock
and a spend cap, and file the outcome in exactly one bucket so a published number can be audited.

Standard library only, on purpose — see ``instances`` for the leak rule, ``exec`` for how a
project's tests reach the instance image, ``outcome`` and ``ledger`` for the accounting.
"""

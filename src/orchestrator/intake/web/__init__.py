"""Block B web preview — paste a Confluence URL, see the backlog proposal.

A minimal read-only surface over ``BacklogService.analyze``: it derives
intents, flags gaps, and drafts feature specs, then renders the would-be
Jira backlog *before anything is written*. Embodies the read-only-default
adoption lever — this app never calls ``create_issues``; live creation stays
with the CLI behind the intent-approval bookend.
"""

from orchestrator.intake.web.app import create_app

__all__ = ["create_app"]

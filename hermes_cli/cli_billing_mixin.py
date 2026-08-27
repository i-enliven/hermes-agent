"""
Billing and subscription handlers for the interactive CLI.

This module previously hosted the Nous billing/subscription methods lifted
out of ``cli.py``'s ``HermesCLI`` class. The Nous cloud integration has been
removed, so those handlers (``_show_subscription``, ``_show_billing``,

The class is retained as an empty mixin for import-compatibility with any
external code that referenced ``CLIBillingMixin``; ``cli.py`` no longer
inherits from it.
"""

from __future__ import annotations


class CLIBillingMixin:
    """Empty mixin — the Nous billing/subscription CLI surface was removed."""

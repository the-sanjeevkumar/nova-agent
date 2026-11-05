"""``nova setup`` subcommand parser."""

import argparse

from typing import Callable


def build_setup_parser(subparsers, *, cmd_setup: Callable) -> None:
    """Attach the ``setup`` subcommand to ``subparsers``."""
    setup_parser = subparsers.add_parser(
        "setup", help="Interactive setup wizard",
        description="Configure Nova Agent with an interactive wizard. "
        "Run a specific section: "
        "nova setup model|tts|terminal|gateway|tools|telemetry|agent")
    setup_parser.add_argument(
        "section", nargs="?",
        choices=["model", "tts", "terminal", "gateway", "tools", "telemetry", "agent"],
        default=None, help="Run a specific setup section instead of the full wizard")
    setup_parser.add_argument(
        "--non-interactive", action="store_true",
        help="Non-interactive mode (use defaults/env vars)")
    setup_parser.add_argument(
        "--reset", action="store_true", help="Reset configuration to defaults")
    setup_parser.add_argument(
        "--reconfigure", action="store_true",
        help="(Default on existing installs.) Re-run the full wizard, "
        "showing current values as defaults. Kept for backwards "
        "compatibility — a bare 'nova setup' now does this.")
    setup_parser.add_argument(
        "--quick", action="store_true",
        help="On existing installs: only prompt for items that are missing "
        "or unset, instead of running the full reconfigure wizard.")
    # Deliberately kept functional for existing users/scripts, but suppressed from help:
    # the setup flow must not advertise Nous.
    setup_parser.add_argument(
        "--portal", action="store_true", help=argparse.SUPPRESS)
    setup_parser.set_defaults(func=cmd_setup)

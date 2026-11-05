"""Behavioral contracts for the Nova CLI alias and its help output."""

import sys


def test_nova_help_uses_nova_commands_and_product_name(monkeypatch):
    from nova_cli._parser import build_top_level_parser

    monkeypatch.setattr(sys, "argv", ["nova"])
    parser, _subparsers, _chat = build_top_level_parser()
    help_text = parser.format_help()

    assert "usage: nova" in help_text
    assert "nova setup" in help_text
    assert "Nova Agent" not in help_text
    assert "hermes setup" not in help_text


def test_hermes_launcher_name_remains_a_compatibility_alias(monkeypatch):
    from nova_cli._parser import build_top_level_parser

    monkeypatch.setattr(sys, "argv", ["hermes"])
    parser, _subparsers, _chat = build_top_level_parser()

    assert parser.prog == "hermes"
    assert "hermes setup" in parser.format_help()

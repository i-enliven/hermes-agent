"""Removal guards: the `hermes claw` OpenClaw-migration command is gone."""

from hermes_cli import main as main_mod
from hermes_cli import setup as setup_mod


def test_claw_not_a_builtin_subcommand():
    assert "claw" not in main_mod._BUILTIN_SUBCOMMANDS
    assert not hasattr(main_mod, "cmd_claw")


def test_no_openclaw_migration_in_setup():
    assert not hasattr(setup_mod, "_offer_openclaw_migration")
    assert not hasattr(setup_mod, "_OPENCLAW_SCRIPT")
    assert not hasattr(setup_mod, "_load_openclaw_migration_module")
    assert not hasattr(setup_mod, "_skip_configured_section")


def test_no_pre_migration_backup_api():
    from hermes_cli import backup as backup_mod

    assert not hasattr(backup_mod, "create_pre_migration_backup")
    assert not hasattr(backup_mod, "_prune_pre_migration_backups")


def test_no_onboarding_residue_api():
    from agent import onboarding as onb

    assert not hasattr(onb, "detect_openclaw_residue")
    assert not hasattr(onb, "openclaw_residue_hint_cli")
    assert not hasattr(onb, "OPENCLAW_RESIDUE_FLAG")

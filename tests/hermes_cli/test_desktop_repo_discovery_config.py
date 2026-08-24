from hermes_cli.config import DEFAULT_CONFIG


def test_desktop_repo_discovery_defaults_preserve_existing_behavior():
    desktop = DEFAULT_CONFIG["desktop"]

    assert desktop["repo_scan_enabled"] is True
    assert desktop["repo_scan_roots"] == []
    assert desktop["repo_scan_exclude_paths"] == []

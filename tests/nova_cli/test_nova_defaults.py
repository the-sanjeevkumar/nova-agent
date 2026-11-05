"""Nova's default setup avoids automatic hosted-account provisioning."""


def test_nous_guest_provisioning_is_opt_in_by_default():
    from nova_cli.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["nous"]["guest"] is False


def test_new_model_picker_hides_nous_provider():
    from nova_cli.config import load_config
    from nova_cli.main_provider_setup import _build_provider_picker_rows

    rows, _ = _build_provider_picker_rows(load_config(), "", {}, {})

    assert not any(key == "nous" for key, _label, _members in rows)


def test_nova_does_not_enable_inherited_source_updates_by_default():
    from nova_cli.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["updates"]["enabled"] is False
    assert DEFAULT_CONFIG["updates"]["check"] is False

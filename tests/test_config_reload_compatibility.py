import importlib


def test_config_reload_rebuilds_beta_exception_points_when_rules_is_stale(monkeypatch):
    import config
    import rules

    expected = tuple(rules.BETA_COMPARISON_SALES_POINTS)
    monkeypatch.delattr(rules, "BETA_ONLY_EXCEPTION_SALES_POINTS", raising=False)

    reloaded_config = importlib.reload(config)

    assert tuple(reloaded_config.BETA_ONLY_EXCEPTION_SALES_POINTS) == expected

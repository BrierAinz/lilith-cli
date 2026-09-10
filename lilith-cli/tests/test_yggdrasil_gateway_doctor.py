from __future__ import annotations

from types import SimpleNamespace


def test_gateway_doctor_warns_when_not_configured(monkeypatch):
    from lilith_cli import main as cli_main
    from lilith_tools import yggdrasil_gateway

    monkeypatch.setattr(
        yggdrasil_gateway,
        "probe_gateway",
        lambda: SimpleNamespace(configured=False, available=False, mode="shadow", error=""),
    )

    row = cli_main._check_yggdrasil_gateway()

    assert row["check"] == "yggdrasil.gateway"
    assert row["status"] == "warn"
    assert "sin configurar" in row["message"]


def test_gateway_doctor_reports_shadow_without_claiming_enforcement(monkeypatch):
    from lilith_cli import main as cli_main
    from lilith_tools import yggdrasil_gateway

    monkeypatch.setattr(
        yggdrasil_gateway,
        "probe_gateway",
        lambda: SimpleNamespace(
            configured=True,
            available=True,
            mode="shadow",
            account_count=4,
            error="",
        ),
    )

    row = cli_main._check_yggdrasil_gateway()

    assert row["status"] == "warn"
    assert "4 cuentas" in row["message"]
    assert "solo observacion" in row["message"]


def test_gateway_doctor_reports_enforce_as_active(monkeypatch):
    from lilith_cli import main as cli_main
    from lilith_tools import yggdrasil_gateway

    monkeypatch.setattr(
        yggdrasil_gateway,
        "probe_gateway",
        lambda: SimpleNamespace(
            configured=True,
            available=True,
            mode="enforce",
            account_count=6,
            error="",
        ),
    )

    row = cli_main._check_yggdrasil_gateway()

    assert row["status"] == "ok"
    assert "6 cuentas" in row["message"]
    assert "seleccion activa" in row["message"]


def test_gateway_doctor_fails_loud_when_configured_but_broken(monkeypatch):
    from lilith_cli import main as cli_main
    from lilith_tools import yggdrasil_gateway

    monkeypatch.setattr(
        yggdrasil_gateway,
        "probe_gateway",
        lambda: SimpleNamespace(
            configured=True,
            available=False,
            mode="enforce",
            account_count=0,
            error="contract mismatch",
        ),
    )

    row = cli_main._check_yggdrasil_gateway()

    assert row["status"] == "error"
    assert "contract mismatch" in row["message"]

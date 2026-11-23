"""Dashboard text and alert transitions. No network."""

from datetime import datetime

from dashboard import (
    AlertState,
    charge_restart,
    format_duration,
    render,
    snapshot_from_quota,
    transition_alerts,
)

NOW = datetime(2026, 11, 11, 10, 54, 12)


def sample_quota():
    return {
        "pd.soc": 87,
        "pd.wattsInSum": 0,
        "pd.wattsOutSum": 130,
        "inv.inputWatts": 0,
        "mppt.inWatts": 0,
        "pd.chgSunPower": 9,
        "pd.usb1Watts": 0,
        "pd.usb2Watts": 5,
        "pd.qcUsb1Watts": 0,
        "pd.qcUsb2Watts": 0,
        "pd.remainTime": 1173,
        "pd.sysChgDsgState": 1,
        "bmsMaster.remainCap": 68946,
        "bmsMaster.vol": 49788,
    }


def sample_device(online=1):
    return {"sn": "DCEBZ5SGC081956", "online": online, "productName": "DELTA Pro"}


def test_render_dashboard():
    snapshot = snapshot_from_quota(sample_quota(), sample_device(), updated_at=NOW)
    text = render(snapshot)
    assert "🔋 DELTA Pro" in text
    assert "🟢 онлайн" in text
    assert "87% · 3.43 кВт·ч" in text
    assert "🔋" * 9 + "🪫" in text
    assert "⬇️ Вход 0 Вт · ⬆️ Выход 130 Вт" in text
    assert "🔌 Сеть 0 Вт · 💻 USB 5 Вт" in text
    assert "Солнце" not in text
    assert "⏱ Осталось 19 ч 33 мин" in text
    assert "⚡ разряд" in text
    assert "🕐 10:54:12" in text


def test_solar_uses_mppt_input_not_cumulative_energy():
    quota = dict(sample_quota(), **{"mppt.inWatts": 90, "pd.chgSunPower": 9})
    snapshot = snapshot_from_quota(quota, sample_device(), updated_at=NOW)
    assert snapshot.solar == 9
    assert "☀️ Солнце 9 Вт" in render(snapshot)


def test_format_duration():
    assert format_duration(1173) == "19 ч 33 мин"
    assert format_duration(12) == "12 мин"
    assert format_duration(None) == "—"
    assert format_duration(24 * 60) == "24 ч 0 мин"
    assert format_duration(395 * 60 + 29) == "16 д 11 ч"


def test_alerts_fire_once_per_transition():
    first = snapshot_from_quota(sample_quota(), sample_device(), updated_at=NOW)
    alerts, state = transition_alerts(AlertState(), first, low_soc=20)
    assert alerts == []
    assert state.seen

    offline = snapshot_from_quota(sample_quota(), sample_device(online=0), updated_at=NOW)
    alerts, state = transition_alerts(state, offline, low_soc=20)
    assert alerts == ["🔴 DELTA Pro офлайн"]

    charging = dict(sample_quota())
    charging["pd.sysChgDsgState"] = 2
    charging["pd.soc"] = 18
    low = snapshot_from_quota(charging, sample_device(), updated_at=NOW)
    alerts, state = transition_alerts(state, low, low_soc=20)
    assert "🟢 DELTA Pro снова онлайн" in alerts
    assert "🔌 DELTA Pro: заряд" in alerts
    assert "⚠️ Заряд ниже 20%: сейчас 18%" in alerts

    again = snapshot_from_quota(charging, sample_device(), updated_at=NOW)
    alerts, state = transition_alerts(state, again, low_soc=20)
    assert alerts == []

    recovered = dict(charging)
    recovered["pd.soc"] = 25
    back = snapshot_from_quota(recovered, sample_device(), updated_at=NOW)
    alerts, _state = transition_alerts(state, back, low_soc=20)
    assert alerts == ["✅ Заряд снова выше 20%: сейчас 25%"]


def test_matching_watts_show_passthrough():
    close = dict(sample_quota(), **{"pd.wattsInSum": 100, "pd.wattsOutSum": 103, "pd.sysChgDsgState": 1})
    text = render(snapshot_from_quota(close, sample_device(), updated_at=NOW))
    assert "↔️ транзит" in text

    exact_edge = dict(sample_quota(), **{"pd.wattsInSum": 100, "pd.wattsOutSum": 105, "pd.sysChgDsgState": 1})
    assert "↔️ транзит" in render(snapshot_from_quota(exact_edge, sample_device(), updated_at=NOW))

    apart = dict(sample_quota(), **{"pd.wattsInSum": 100, "pd.wattsOutSum": 106, "pd.sysChgDsgState": 1})
    assert "⚡ разряд" in render(snapshot_from_quota(apart, sample_device(), updated_at=NOW))


def test_passthrough_alert_replaces_false_discharge():
    charging = dict(sample_quota(), **{"pd.wattsInSum": 400, "pd.wattsOutSum": 20, "pd.sysChgDsgState": 2})
    _alerts, state = transition_alerts(
        AlertState(),
        snapshot_from_quota(charging, sample_device(), updated_at=NOW),
        low_soc=20,
    )
    done = dict(sample_quota(), **{"pd.wattsInSum": 100, "pd.wattsOutSum": 103, "pd.sysChgDsgState": 1})
    alerts, state = transition_alerts(
        state,
        snapshot_from_quota(done, sample_device(), updated_at=NOW),
        low_soc=20,
    )
    assert alerts == ["↔️ DELTA Pro: транзит"]
    assert state.visible_label == "транзит"


def test_discharge_decade_messages():
    start = dict(sample_quota(), **{"pd.soc": 83, "pd.wattsInSum": 0, "pd.wattsOutSum": 100, "pd.sysChgDsgState": 1})
    _alerts, state = transition_alerts(
        AlertState(),
        snapshot_from_quota(start, sample_device(), updated_at=NOW),
        low_soc=20,
    )
    assert state.discharge_decade is None

    at_eighty = dict(start, **{"pd.soc": 80})
    alerts, state = transition_alerts(
        state,
        snapshot_from_quota(at_eighty, sample_device(), updated_at=NOW),
        low_soc=20,
    )
    assert alerts == ["🔋 Осталось 80%"]

    alerts, state = transition_alerts(
        state,
        snapshot_from_quota(at_eighty, sample_device(), updated_at=NOW),
        low_soc=20,
    )
    assert alerts == []

    charging = dict(at_eighty, **{"pd.sysChgDsgState": 2, "pd.wattsInSum": 400})
    alerts, state = transition_alerts(
        state,
        snapshot_from_quota(charging, sample_device(), updated_at=NOW),
        low_soc=20,
    )
    assert "🔋 Осталось 80%" not in alerts
    assert charge_restart(AlertState(seen=True, visible_label="разряд"), state)


def test_grid_alerts_only_when_mains_changes():
    with_grid = dict(sample_quota(), **{"inv.acInVol": 230000})
    first = snapshot_from_quota(with_grid, sample_device(), updated_at=NOW)
    alerts, state = transition_alerts(AlertState(), first, low_soc=20)
    assert alerts == []
    assert state.grid_present is True

    outage = snapshot_from_quota(dict(sample_quota(), **{"inv.acInVol": 0}), sample_device(), updated_at=NOW)
    alerts, state = transition_alerts(state, outage, low_soc=20)
    assert alerts == ["Света вышла из чата!"]

    again = snapshot_from_quota(dict(sample_quota(), **{"inv.acInVol": 0}), sample_device(), updated_at=NOW)
    alerts, state = transition_alerts(state, again, low_soc=20)
    assert alerts == []

    restored = snapshot_from_quota(with_grid, sample_device(), updated_at=NOW)
    alerts, state = transition_alerts(state, restored, low_soc=20)
    assert alerts == ["Света в здании!"]
    assert state.grid_present is True


def test_api_error_does_not_alert():
    failed = snapshot_from_quota({}, error="timeout", updated_at=NOW)
    alerts, state = transition_alerts(AlertState(seen=True, online=True), failed, low_soc=20)
    assert alerts == []
    assert state.online is True
    assert "⚠️ Не удалось получить данные" in render(failed)
    assert "timeout" in render(failed)


def test_soc_bar_uses_ten_batteries():
    from dashboard import soc_bar

    assert soc_bar(0) == "🪫" * 10
    assert soc_bar(34) == "🔋" * 3 + "🪫" * 7
    assert soc_bar(100) == "🔋" * 10
    assert soc_bar(None) == "🪫" * 10


def test_energy_missing_shows_dash():
    quota = sample_quota()
    del quota["bmsMaster.remainCap"]
    text = render(snapshot_from_quota(quota, sample_device(), updated_at=NOW))
    assert "87% · — кВт·ч" in text


def test_clock_uses_dashboard_timezone(monkeypatch):
    from dashboard import local_now

    monkeypatch.setenv("DASHBOARD_TZ", "Europe/Kyiv")
    assert local_now().tzinfo.key == "Europe/Kyiv"
    monkeypatch.setenv("DASHBOARD_TZ", "Not/AZone")
    assert local_now().tzinfo.key == "Europe/Kyiv"

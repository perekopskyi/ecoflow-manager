"""DELTA Pro dashboard snapshot and Telegram text."""

import os
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

POWER_LABELS = {0: "простой", 1: "разряд", 2: "заряд"}
POWER_EMOJI = {0: "⏸", 1: "⚡", 2: "🔌"}
PASSTHROUGH_LABEL = "транзит"
PASSTHROUGH_EMOJI = "↔️"
PASSTHROUGH_WATTS = 5
DEFAULT_TIMEZONE = "Europe/Kyiv"


def local_timezone():
    """Timezone for the dashboard clock: DASHBOARD_TZ, else Europe/Kyiv."""
    name = os.environ.get("DASHBOARD_TZ", "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_now():
    return datetime.now(local_timezone())


def _number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _watts(*values):
    total = 0.0
    seen = False
    for value in values:
        number = _number(value)
        if number is None:
            continue
        total += number
        seen = True
    if not seen:
        return None
    return int(round(total))


def format_duration(minutes):
    number = _number(minutes)
    if number is None:
        return "—"
    whole = max(0, int(round(number)))
    hours, mins = divmod(whole, 60)
    if hours > 24 or (hours == 24 and mins > 0):
        days, hours = divmod(hours, 24)
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {mins} мин"
    return f"{mins} мин"


def soc_bar(soc):
    """Ten batteries: 🔋 per charged 10%, 🪫 per empty 10%."""
    number = _number(soc)
    if number is None:
        return "🪫" * 10
    filled = max(0, min(10, int(round(number / 10))))
    return "🔋" * filled + "🪫" * (10 - filled)


def energy_kwh(remain_mah, voltage_mv):
    """Stored energy: remaining capacity (mAh) times pack voltage (mV)."""
    capacity = _number(remain_mah)
    voltage = _number(voltage_mv)
    if capacity is None or voltage is None:
        return None
    return capacity * voltage / 1_000_000_000


def format_kwh(value):
    if value is None:
        return "—"
    return f"{value:.2f}"


def format_soc(soc):
    number = _number(soc)
    if number is None:
        return "—"
    if abs(number - round(number)) < 0.05:
        return str(int(round(number)))
    return f"{number:.1f}"


@dataclass
class Snapshot:
    product_name: str
    online: bool | None
    soc: float | None
    energy_kwh: float | None
    watts_in: int | None
    watts_out: int | None
    ac_in: int | None
    solar: int | None
    usb: int | None
    remain_minutes: float | None
    power_state: int | None
    grid_present: bool | None
    updated_at: datetime
    error: str | None = None

    @property
    def ok(self):
        return self.error is None


def grid_present(voltage):
    """True when mains voltage is present.

    DELTA Pro reports inv.acInVol in millivolts (230000). A reading below
    1000 is already in volts. Below 80 V the grid is treated as gone.
    """
    number = _number(voltage)
    if number is None:
        return None
    if number >= 1000:
        number = number / 1000
    return number >= 80


def _scaled_watts(value, factor):
    number = _number(value)
    if number is None:
        return None
    return int(round(number * factor))


def snapshot_from_quota(quota, device=None, updated_at=None, error=None):
    values = quota or {}
    name = "DELTA Pro"
    online = None
    if device:
        name = device.get("productName") or name
        if "online" in device:
            online = int(device.get("online") or 0) == 1
    state = _number(values.get("pd.sysChgDsgState"))
    return Snapshot(
        product_name=name,
        online=online,
        soc=_number(values.get("pd.soc")),
        energy_kwh=energy_kwh(
            values.get("bmsMaster.remainCap"),
            values.get("bmsMaster.vol"),
        ),
        watts_in=_watts(values.get("pd.wattsInSum")),
        watts_out=_watts(values.get("pd.wattsOutSum")),
        ac_in=_watts(values.get("inv.inputWatts")),
        solar=_scaled_watts(values.get("mppt.inWatts"), 0.1),
        usb=_watts(
            values.get("pd.usb1Watts"),
            values.get("pd.usb2Watts"),
            values.get("pd.qcUsb1Watts"),
            values.get("pd.qcUsb2Watts"),
        ),
        remain_minutes=_number(values.get("pd.remainTime")),
        power_state=None if state is None else int(state),
        grid_present=grid_present(values.get("inv.acInVol")),
        updated_at=updated_at or local_now(),
        error=error,
    )


def fetch_snapshot(client, updated_at=None):
    try:
        device = client.device_record()
        quota = client.get_quota().get("data") or {}
    except Exception as exc:
        return snapshot_from_quota({}, error=str(exc), updated_at=updated_at)
    if device is None:
        return snapshot_from_quota(
            quota,
            device={"productName": "DELTA Pro", "online": 0, "sn": client.sn},
            updated_at=updated_at,
        )
    return snapshot_from_quota(quota, device=device, updated_at=updated_at)


def visible_status(snapshot):
    """Passthrough when input and output watts match within 5 W."""
    if (
        snapshot.watts_in is not None
        and snapshot.watts_out is not None
        and abs(snapshot.watts_in - snapshot.watts_out) <= PASSTHROUGH_WATTS
    ):
        return PASSTHROUGH_EMOJI, PASSTHROUGH_LABEL
    return (
        POWER_EMOJI.get(snapshot.power_state, "❔"),
        POWER_LABELS.get(snapshot.power_state, "неизвестно"),
    )


def _watts_text(value):
    if value is None:
        return "—"
    return str(value)


def render(snapshot):
    if snapshot.online is True:
        presence = "🟢 онлайн"
    elif snapshot.online is False:
        presence = "🔴 офлайн"
    else:
        presence = "⚪ нет связи с API"
    lines = [f"🔋 {snapshot.product_name}", presence, ""]
    if not snapshot.ok:
        lines.append("⚠️ Не удалось получить данные")
        lines.append(snapshot.error or "неизвестная ошибка")
        return "\n".join(lines)

    state_emoji, state = visible_status(snapshot)
    sources = [f"🔌 Сеть {_watts_text(snapshot.ac_in)} Вт"]
    if snapshot.solar:
        sources.append(f"☀️ Солнце {_watts_text(snapshot.solar)} Вт")
    sources.append(f"💻 USB {_watts_text(snapshot.usb)} Вт")
    lines.extend(
        [
            f"{format_soc(snapshot.soc)}% · {format_kwh(snapshot.energy_kwh)} кВт·ч",
            soc_bar(snapshot.soc),
            "",
            f"⬇️ Вход {_watts_text(snapshot.watts_in)} Вт · ⬆️ Выход {_watts_text(snapshot.watts_out)} Вт",
            " · ".join(sources),
            f"⏱ Осталось {format_duration(snapshot.remain_minutes)}",
            f"{state_emoji} {state}",
            "",
            f"🕐 {snapshot.updated_at.strftime('%H:%M:%S')}",
        ]
    )
    return "\n".join(lines)


@dataclass
class AlertState:
    seen: bool = False
    online: bool | None = None
    power_state: int | None = None
    visible_label: str | None = None
    below_threshold: bool | None = None
    grid_present: bool | None = None
    discharge_decade: int | None = None


def transition_alerts(previous, snapshot, low_soc):
    """Return alert texts and the next state. The first good sample only records."""
    if not snapshot.ok or snapshot.online is None or snapshot.soc is None:
        return [], previous

    below = snapshot.soc < low_soc
    grid = snapshot.grid_present
    if grid is None:
        grid = previous.grid_present
    _emoji, label = visible_status(snapshot)
    rounded_soc = int(round(snapshot.soc))
    on_decade = label == POWER_LABELS[1] and rounded_soc % 10 == 0
    current = AlertState(
        seen=True,
        online=snapshot.online,
        power_state=snapshot.power_state,
        visible_label=label,
        below_threshold=below,
        grid_present=grid,
        discharge_decade=rounded_soc if on_decade else None,
    )
    if not previous.seen:
        return [], current

    alerts = []
    if on_decade and previous.discharge_decade != rounded_soc:
        alerts.append(f"🔋 Осталось {rounded_soc}%")
    if previous.online is not None and snapshot.online != previous.online:
        if snapshot.online:
            alerts.append(f"🟢 {snapshot.product_name} снова онлайн")
        else:
            alerts.append(f"🔴 {snapshot.product_name} офлайн")

    if previous.visible_label is not None and label != previous.visible_label:
        alerts.append(f"{_emoji} {snapshot.product_name}: {label}")

    if previous.grid_present is not None and grid is not None and grid != previous.grid_present:
        if grid:
            alerts.append("Света в здании!")
        else:
            alerts.append("Света вышла из чата!")

    if previous.below_threshold is not None and below != previous.below_threshold:
        soc = format_soc(snapshot.soc)
        if below:
            alerts.append(f"⚠️ Заряд ниже {low_soc:g}%: сейчас {soc}%")
        else:
            alerts.append(f"✅ Заряд снова выше {low_soc:g}%: сейчас {soc}%")

    return alerts, current


def charge_restart(previous, current):
    """True when the visible status just became charging."""
    return (
        previous.seen
        and current.visible_label == POWER_LABELS[2]
        and previous.visible_label != POWER_LABELS[2]
    )

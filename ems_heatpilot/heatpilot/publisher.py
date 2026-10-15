"""Ergebnisse als ``sensor.heatpilot_*`` in Home Assistant bereitstellen."""

from __future__ import annotations

from .ha_client import HAClient


def _sensor(state, name, unit=None, icon=None, device_class=None, **extra):
    attrs = {"friendly_name": f"HeatPilot {name}", **extra}
    if unit:
        attrs["unit_of_measurement"] = unit
        attrs["state_class"] = "measurement"
    if icon:
        attrs["icon"] = icon
    if device_class:
        attrs["device_class"] = device_class
    return ("unknown" if state is None else state), attrs


async def publish(ha: HAClient, s: dict) -> None:
    house, cyc, sav = s["house"], s["cycling"], s["savings"]
    plan = s.get("plan") or {}
    best = plan.get("best") or {}
    arrival = s.get("arrival") or {}
    sensors = {
        "sensor.heatpilot_waermeverlust": _sensor(
            house["heat_loss_w_per_k"], "Wärmeverlust", "W/K", "mdi:home-thermometer-outline",
            lernschritte=house["updates"], modellfehler_kw=house["rmse_kw"],
        ),
        "sensor.heatpilot_heizlast_aktuell": _sensor(
            house["demand_now_kw"], "Heizlast aktuell", "kW", "mdi:fire", device_class="power",
        ),
        "sensor.heatpilot_normheizlast": _sensor(
            house["design_load_kw"], "Normheizlast", "kW", "mdi:snowflake-thermometer", device_class="power",
        ),
        "sensor.heatpilot_haus_traegheit": _sensor(
            house["tau_h"], "Haus-Trägheit", "h", "mdi:timer-sand",
        ),
        "sensor.heatpilot_vorlauf_empfehlung": _sensor(
            s.get("flow_recommendation"), "Vorlauf-Empfehlung", "°C", "mdi:thermometer-chevron-up",
            device_class="temperature", aktueller_sollwert=s.get("flow_current_setpoint"),
        ),
        "sensor.heatpilot_brennerstarts_heute": _sensor(
            cyc["starts"], "Brennerstarts heute", "Starts", "mdi:counter",
            mittlere_laufzeit_min=cyc["avg_run_min"], mittlere_pause_min=cyc["avg_pause_min"],
            kurzlaeufer=cyc["short_runs"],
        ),
        "sensor.heatpilot_taktsperre_wartezeit_heute": _sensor(
            cyc["blocked_min"], "Wartezeit durch Taktsperre heute", "min", "mdi:timer-lock-outline",
            raeume_warten_gesamt_min=cyc["waiting_min"],
        ),
        "sensor.heatpilot_nachlauf_ohne_bedarf_heute": _sensor(
            cyc["overrun_min"], "Brenner ohne Wärmebedarf heute", "min", "mdi:fire-alert",
            gas_kwh=cyc["overrun_kwh"],
        ),
        "sensor.heatpilot_laufzeit_je_start": _sensor(
            cyc.get("avg_run_min"), "Laufzeit je Heizstart heute", "min", "mdi:timer-outline",
            gesamt_seit_einbau_min=cyc.get("lifetime_run_per_start_min"),
        ),
        "sensor.heatpilot_gaskosten": _sensor(
            s["costs"]["eur"], "Gaskosten (letzte 12 Monate)", "€", "mdi:currency-eur",
            kwh_ausgewertet=s["costs"]["kwh"], tage_ausgewertet=s["costs"]["days"],
            volles_jahr=s["costs"]["full_year"],
        ),
        "sensor.heatpilot_ersparnis_schaetzung": _sensor(
            sav["saved_pct"], "Ersparnis-Schätzung", "%", "mdi:piggy-bank-outline",
            kwh_im_zeitraum=sav["saved_kwh"], euro_im_zeitraum=sav["saved_eur"],
            kwh_pro_jahr=sav["year_kwh"], euro_pro_jahr=sav["year_eur"],
            ausgewertete_tage=sav["span_days"],
        ),
        "sensor.heatpilot_naechste_ankunft": _sensor(
            arrival.get("eta"), "Nächste Ankunft", icon="mdi:home-import-outline",
            device_class="timestamp", person=arrival.get("person"), quelle=arrival.get("source"),
        ),
        "sensor.heatpilot_vorheizen_start": _sensor(
            best.get("start"), "Vorheizen ab", icon="mdi:radiator", device_class="timestamp",
            vorlauf=best.get("flow"), dauer_h=best.get("duration_h"), gas_kwh=best.get("est_kwh"),
        ),
    }
    for entity_id, (state, attrs) in sensors.items():
        await ha.set_state(entity_id, state, attrs)

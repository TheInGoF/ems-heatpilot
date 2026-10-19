"""Benachrichtigungen (seit 0.38): Meldungen an die eingestellten Ziele in Home Assistant und der Problem-Sensor.

Ziele stehen in der Einstellung ``notify_targets`` (nur ``notify.*``). Zwei Arten, beide in der Auswahl der
Oberfläche (``available``):

* **Entität** der Domäne ``notify`` (Home Assistant ab 2024.6) – geschickt mit der Aktion ``notify.send_message``
  und ``entity_id``,
* **Dienst** wie ``notify.mobile_app_handy`` (die Handy-App, ältere Integrationen, auch ohne notify-Entitäten) –
  geschickt als Dienst ``notify.<name>`` mit ``message`` und ``title``.

Geschickt wird nur über ``Control.notify`` (dort hart auf die eingestellten Ziele begrenzt); Titel „HeatPilot“, Text
kurz und konkret. Wer etwas melden will, öffnet ein Problem unter einem Schlüssel (``problem``, z. B.
„dhw_write_failed“) und schließt es wieder (``solved``). Entprellt: Gemeldet wird einmal je Ereignis – beim Öffnen,
nicht in jedem Takt – und beim Schließen einmal „wieder in Ordnung“ (nur, wenn das Problem gemeldet wurde);
höchstens eine Problem-Meldung je Schlüssel in 6 h. Der Stand überlebt Neustarts (kv ``notify.state``).

Offene Probleme zeigt der Problem-Sensor ``binary_sensor.heatpilot_problem`` (``problem_state``, gesetzt von
``publisher.publish_problem``) mit den Gründen im Attribut ``reasons`` – auch ohne eingestellte Ziele.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

TITLE = "HeatPilot"
REPEAT_S = 6 * 3600          # höchstens eine Problem-Meldung je Schlüssel in dieser Zeit
KV = "notify.state"
KV_LAST = "notify.last"      # Ergebnis der letzten Sendung (für die Oberfläche)
OK_SUFFIX = ":ok"
TEST_TEXT = "Testnachricht – Benachrichtigungen von HeatPilot kommen hier an."
NO_TARGETS = "Keine Ziele gespeichert – erst wählen und speichern"
KEEP_CLOSED_S = 7 * 86400    # geschlossene Schlüssel so lange merken (für die 6-h-Sperre reicht weniger)


def kind_of(target: str, states: dict) -> str:
    """„entity“, wenn es die notify-Entität in Home Assistant gibt, sonst „service“ (Dienst ``notify.<name>``)."""
    return "entity" if target in (states or {}) else "service"


def available(states: dict, services: dict | None, selected=()) -> list[dict]:
    """Auswahl für die Oberfläche: notify-Entitäten aus den Zuständen, Dienste aus ``get_services`` (ohne
    ``send_message``, das nur mit Entität geht). Je Eintrag {id, name, kind, selected}; gewählte Ziele, die es
    nicht (mehr) gibt, stehen mit ``kind`` „fehlt“ dabei, damit man sie abwählen kann."""
    selected = list(selected or [])
    out: dict[str, dict] = {}
    for eid, st in sorted((states or {}).items()):
        if eid.startswith("notify."):
            name = (st.get("attributes") or {}).get("friendly_name") or eid
            out[eid] = {"id": eid, "name": name, "kind": "entity"}
    for name in sorted((services or {}).get("notify") or {}):
        eid = f"notify.{name}"
        if name != "send_message" and eid not in out:
            out[eid] = {"id": eid, "name": name.replace("_", " "), "kind": "service"}
    for eid in selected:
        out.setdefault(eid, {"id": eid, "name": eid, "kind": "fehlt"})
    return [{**v, "selected": v["id"] in selected} for v in out.values()]


class Notifier:
    """Offene Probleme je Schlüssel, Warteschlange der Meldungen und das Senden an die Ziele."""

    def __init__(self, store, control=None, targets=(), carry: Notifier | None = None) -> None:
        """``carry``: Notifier vor einem Neuaufbau – Stand und Warteschlange gehen über (dieselben Objekte)."""
        self.store = store
        self.control = control
        self.targets: list[str] = [t for t in targets or () if str(t).startswith("notify.")]
        self.state: dict[str, dict] = carry.state if carry is not None else dict(store.get(KV) or {})
        self.queue: list[tuple[str, str]] = carry.queue if carry is not None else []
        self.last: dict = carry.last if carry is not None else dict(store.get(KV_LAST) or {})

    # ------------------------------------------------------------------ Probleme
    def problem(self, key: str, text: str, ts: float | None = None) -> bool:
        """Problem öffnen bzw. seinen Text auffrischen. Gemeldet wird nur beim Öffnen und höchstens alle 6 h je
        Schlüssel. True, wenn eine Meldung vorgemerkt wurde."""
        ts = time.time() if ts is None else ts
        e = self.state.get(key)
        if e and e.get("open"):
            if e.get("text") != text:
                e["text"] = text
                self._save()
            return False
        sent = (e or {}).get("sent")
        send = bool(self.targets) and (sent is None or ts - sent >= REPEAT_S)
        self.state[key] = {"open": True, "text": text, "since": ts, "sent": ts if send else sent, "notified": send}
        if send:
            self.queue.append((key, text))
        log.warning("Problem: %s%s", text, "" if send else " (keine Meldung: kein Ziel bzw. schon gemeldet)")
        self._save()
        return send

    def solved(self, key: str, text: str | None = None, ts: float | None = None, quiet: bool = False) -> bool:
        """Problem schließen. „Wieder in Ordnung“ (``text``) nur, wenn das Problem gemeldet wurde und nicht ``quiet``
        (z. B. Schalter umgestellt, jemand hat selbst eingegriffen). True, wenn eine Meldung vorgemerkt wurde."""
        e = self.state.get(key)
        if not e or not e.get("open"):
            return False
        e["open"], e["closed"] = False, time.time() if ts is None else ts
        send = bool(e.get("notified") and not quiet and text and self.targets)
        if send:
            self.queue.append((key + OK_SUFFIX, text))
        log.info("Problem erledigt: %s%s", e.get("text"), "" if send else " (ohne Meldung)")
        self._save()
        return send

    def open_keys(self, prefix: str = "") -> dict[str, str]:
        """Offene Probleme {Schlüssel: Text}, optional nur mit diesem Anfang."""
        return {k: e.get("text") or k for k, e in self.state.items() if e.get("open") and k.startswith(prefix)}

    def settle(self, prefix: str, keep: dict[str, str], ts: float | None = None) -> None:
        """Abgleich je Thema: Probleme mit ``prefix``, die nicht in ``keep`` stehen, still schließen (Raum entfernt,
        Schalter aus). Offene Texte aus ``keep`` auffrischen, ohne neu zu melden."""
        for key in list(self.open_keys(prefix)):
            if key not in keep:
                self.solved(key, ts=ts, quiet=True)
            elif self.state[key].get("text") != keep[key]:
                self.state[key]["text"] = keep[key]
                self._save()

    def problem_state(self) -> dict:
        """Für den Problem-Sensor: {on, reasons} – die Texte aller offenen Probleme, ältestes zuerst."""
        items = sorted(((e.get("since") or 0, e.get("text") or k) for k, e in self.state.items() if e.get("open")))
        reasons = list(dict.fromkeys(t for _, t in items))
        return {"on": bool(reasons), "reasons": reasons}

    def _save(self) -> None:
        now = time.time()
        for k in [k for k, e in self.state.items()  # im selben Objekt aufräumen (geteilt mit ``carry``)
                  if not e.get("open") and now - (e.get("closed") or e.get("sent") or now) >= KEEP_CLOSED_S]:
            del self.state[k]
        self.store.put(KV, self.state)

    # ------------------------------------------------------------------ Senden
    async def _send_all(self, text: str) -> tuple[list[str], list[str]]:
        sent, errors = [], []
        for target in self.targets:
            try:
                if self.control is None:
                    raise RuntimeError("keine Verbindung zum Senden")
                await self.control.notify(target, TITLE, text)
                sent.append(target)
            except Exception as err:  # noqa: BLE001 – je Ziel melden, die anderen trotzdem
                errors.append(f"{target}: {err}")
        return sent, errors

    async def flush(self) -> int:
        """Vorgemerkte Meldungen an alle Ziele schicken (einmal versucht, Fehler nur geloggt). Gibt die Zahl der
        Meldungen zurück."""
        if not self.queue:
            return 0
        items, self.queue[:] = list(self.queue), []
        for key, text in items:
            sent, errors = await self._send_all(text)
            log.info("Meldung „%s“ an %s%s", text, ", ".join(sent) or "niemand",
                     f" · Fehler: {'; '.join(errors)}" if errors else "")
            self._remember(key, text, sent, errors)
        return len(items)

    async def test(self) -> dict:
        """Testnachricht an die eingestellten Ziele (nicht entprellt). {ok, sent, errors}."""
        if not self.targets:
            return {"ok": False, "sent": [], "errors": [NO_TARGETS]}
        sent, errors = await self._send_all(TEST_TEXT)
        self._remember("test", TEST_TEXT, sent, errors)
        return {"ok": not errors, "sent": sent, "errors": errors}

    def _remember(self, key: str, text: str, sent: list[str], errors: list[str]) -> None:
        self.last = {"ts": int(time.time()), "key": key, "text": text, "sent": sent, "errors": errors}
        self.store.put(KV_LAST, self.last)

    # ------------------------------------------------------------------ Anzeige
    def as_dict(self) -> dict:
        """Für die Oberfläche: Ziele, offene Probleme, letzte Sendung, Sperrzeit."""
        return {"targets": list(self.targets), "problems": self.problem_state()["reasons"], "last": self.last or None,
                "repeat_h": REPEAT_S // 3600, "title": TITLE}

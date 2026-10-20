"""Ventilverhalten in Worten (seit 0.39): der Satz je Raum, den ``ValveModel.summary`` mitgibt.

Beispiele: „öffnet ab 0,2 K unter Soll, halb offen bei 0,6 K, ganz bei 0,9 K · reagiert auf +0,5 K in ≈ 10 min,
schließt in ≈ 14 min · Schritt 0,5 K · belastbar“; mit Integrator „regelt mit Integrator, Ruhepunkt am Soll (Kp 50 %/K,
Nachstellzeit 120 min) – die Kennlinie hängt von der Last ab · …“; nur an/aus „schaltet ein bei 0,3 K unter Soll, aus
bei 0,1 K über Soll · …“; Gruppen mit „2 Ventile, Mittel: …“. Minuten werden wie in der Oberfläche gerundet (Halbe
auf), damit Satz und Tabelle dieselbe Zahl zeigen. Rechnet nichts, schreibt nichts.
"""

from __future__ import annotations

from .valve_predict import OPEN_PCT


def de_num(v: float, digits: int = 1) -> str:
    return f"{v:.{digits}f}".replace(".", ",")


def whole_min(v: float) -> str:
    """Ganzzahlig, Halbe aufgerundet – wie die Oberfläche (sonst „14 min“ im Satz, „15 min“ in der Tabelle)."""
    return str(int(v + 0.5)) if v >= 0 else str(-int(-v + 0.5))


def rel(e: float) -> str:
    """Regelabweichung als Text: „0,2 K unter Soll“, „0,1 K über Soll“, „am Soll“."""
    if abs(e) < 0.05:
        return "am Soll"
    return f"{de_num(abs(e))} K {'unter' if e > 0 else 'über'} Soll"


def _at(e: float, short: bool) -> str:
    """„bei 0,6 K“ (nur, wenn der Satz schon „… unter Soll“ gesagt hat), „bei 0,1 K über Soll“, „am Soll“."""
    if abs(e) < 0.05:
        return "am Soll"
    return f"bei {de_num(e)} K" if short and e > 0 else f"bei {rel(e)}"


def _group(g: dict | None, binary: bool) -> str:
    """Gruppe: das Ventil ist das Mittel der Geräte – bei an/aus heißt das ein mit dem ersten, aus mit dem letzten."""
    if not g or g.get("n", 1) < 2:
        return ""
    if g.get("mixed"):
        return f"Mittel aus {g['n']} Ventilen verschiedener Art: "
    return f"{g['n']} Ventile, {'ein mit dem ersten, aus mit dem letzten' if binary else 'Mittel'}: "


def _curve(s: dict) -> str:
    """Kennlinie bzw. – mit Integrator – Reglerwerte statt Punkten, die von der Last abhängen."""
    if s.get("regler") == "PI":
        pi = s["pi"]
        tn = f", Nachstellzeit {whole_min(pi['tn_min'])} min" if pi["tn_min"] is not None else ""
        return (f"regelt mit Integrator, Ruhepunkt {rel(pi['e_i'])} (Kp {whole_min(pi['kp'])} %/K{tn}) – "
                "die Kennlinie hängt von der Last ab")
    out = []
    if s["e0"] is not None:
        out.append(f"öffnet ab {rel(s['e0'])}")
    elif s["curve"] and s["curve"][0]["valve"] > OPEN_PCT:
        out.append(f"schon {rel(s['curve'][0]['e'])} offen")
    short = s["e0"] is not None and s["e0"] >= 0.05  # „öffnet ab … unter Soll“ – danach reicht die Zahl
    if s["e50"] is not None:
        out.append(f"halb offen {_at(s['e50'], short)}")
    if s["e100"] is not None:
        out.append(f"ganz {_at(s['e100'], short)}")
    return ", ".join(out) if out else "Kennlinie lernt noch"


def _reaction(s: dict) -> str | None:
    up, down = s["response"]["up"], s["response"]["down"]
    react = []
    if up["t63_min"] is not None:
        react.append(f"reagiert auf +{de_num(s['rules']['jump_k'])} K in ≈ {whole_min(up['t63_min'])} min")
    if down["t63_min"] is not None:
        react.append(f"schließt in ≈ {whole_min(down['t63_min'])} min")
    coarse = [x["step_min"] for x in (up, down) if x.get("coarse")]
    if not react:
        return None
    return ", ".join(react) + (f" (grob – Messpause {whole_min(max(coarse))} min)" if coarse else "")


def text(s: dict) -> str:
    """Ein Satz je Raum aus ``ValveModel.summary`` (ohne „text“)."""
    binary = s["kind"] == "binär"
    if binary:
        sw, up = s["switch"], s["response"]["up"]
        parts = [f"schaltet ein bei {rel(sw['e_on'])}, aus bei {rel(sw['e_off'])}" if sw["e_on"] is not None
                 else "nur an/aus – Schaltpunkte lernen noch"]
        if up["dead_min"] is not None:
            parts.append(f"schaltet nach einem Soll-Sprung in ≈ {whole_min(up['dead_min'])} min")
    else:
        parts = [_curve(s)]
        react = _reaction(s)
        if react:
            parts.append(react)
    parts[0] = _group(s.get("group"), binary) + parts[0]
    step = s["step_k"]
    parts.append(f"Schritt {de_num(step, 2 if round(step, 1) != step else 1)} K"
                 + (" (angenommen)" if s["step_source"] == "angenommen" else ""))
    if s.get("no_pct"):  # 0.45.1: %-Modell, der Raum meldet nur an/aus
        parts.append(f"ruht ({'; '.join(s['reasons'])})")
    else:
        parts.append("belastbar" if s["reliable"] else f"lernt ({'; '.join(s['reasons'])})")
    return " · ".join(parts)

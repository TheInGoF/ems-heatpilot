from datetime import datetime, timedelta

from heatpilot.config import Config
from heatpilot.dhw import DhwModel, DhwShadow

AMB, K_LOSS = 18.0, 0.04          # Speicher kühlt mit 0,04 K/h je K über Umgebung aus
KWH_PER_K = 0.186 / 0.9           # 160 l Wasser, 90 % Wirkungsgrad
CHARGE_KW = 18 * 0.6


def simulate(days=28, start=datetime(2026, 10, 5, 0, 0)):
    """Speicher 55 °C, lädt unter 45 °C nach, Duschen 7 Uhr (−15 K), Abwasch 19 Uhr (−8 K)."""
    m = DhwModel()
    shadow = DhwShadow()
    cfg = Config()
    t, charging = 55.0, False
    loss_real = loss_strat = 0.0
    cold = 0
    dt_h = 5 / 60
    for i in range(days * 288):
        now = start + timedelta(minutes=5 * i)
        t_prev = t
        t -= K_LOSS * (t - AMB) * dt_h
        if now.minute == 0 and now.hour == 7:
            t -= 15
        if now.minute == 0 and now.hour == 19:
            t -= 8
        if not charging and t < 45:
            charging = True
        if charging:
            t += CHARGE_KW / KWH_PER_K * dt_h
            if t >= 55:
                charging = False
        res = m.step(now, dt_h, round(t_prev, 1), round(t, 1), charging, CHARGE_KW if charging else None)
        rec = m.recommend(now, True, None, cfg)
        sh = shadow.step(m, round(t, 1), rec["target"], res["draw_k"], dt_h, cfg.dhw_comfort_temp)
        if i > 14 * 288:  # nach zwei Wochen Lernen vergleichen
            loss_real += res["loss_kwh"]
            loss_strat += sh["loss_kwh"]
            cold += sh["cold_draw"]
    return m, loss_real, loss_strat, cold


def test_dhw_learns_storage_physics_and_profile():
    m, *_ = simulate()
    assert abs(m.ambient - AMB) < 3, m.loss.theta
    assert abs(m.cooling_kh(55) - K_LOSS * (55 - AMB)) < 0.3
    assert abs(m.kwh_per_kelvin() - KWH_PER_K) < 0.05, m.kwh_per_k
    s = m.summary(55)
    hours = s["profile_hours"]
    assert hours[7] > 10 and hours[19] > 5
    assert max(h for i, h in enumerate(hours) if i not in (7, 19)) < 1.5


def test_dhw_recommendation_follows_usage_and_presence():
    m, *_ = simulate()
    cfg = Config()
    early = datetime(2026, 11, 2, 4, 30)  # Nachladen dauert nur Minuten – noch nicht nötig
    assert m.recommend(early, True, None, cfg)["target"] == cfg.dhw_min_temp
    morning = datetime(2026, 11, 2, 6, 30)  # Montag kurz vor der Dusche (−15 K)
    rec = m.recommend(morning, True, None, cfg)
    assert rec["target"] == cfg.dhw_min_temp + 15 or rec["target"] >= cfg.dhw_comfort_temp
    midday = datetime(2026, 11, 2, 12, 0)
    assert m.recommend(midday, True, None, cfg)["target"] == cfg.dhw_min_temp
    away = m.recommend(morning, False, morning + timedelta(hours=9), cfg)
    assert away["target"] == cfg.dhw_min_temp and away["reason"] == "niemand zu Hause"


def test_dhw_strategy_saves_standby_loss_without_cold_showers():
    _, loss_real, loss_strat, cold = simulate()
    assert loss_strat < loss_real
    assert cold == 0


def test_rising_temperature_is_not_learned_as_cooling():
    """Nach dem Laden zieht der Fühler nach, ohne Ladesignal wird geheizt – beides kein Auskühlen."""
    from datetime import datetime, timedelta

    from heatpilot.dhw import DhwModel

    m = DhwModel()
    t0 = datetime(2026, 11, 2, 6, 0)
    temps = [45.0, 50.0, 55.0]  # Ladung
    for i in range(1, len(temps)):
        m.step(t0 + timedelta(minutes=5 * i), 5 / 60, temps[i - 1], temps[i], True, 15.0)
    before = m.loss.updates
    t = 55.0
    for i in range(6):  # 30 min nach Ladeende: Fühler steigt noch 0,4 K je 5 min
        m.step(t0 + timedelta(minutes=20 + 5 * i), 5 / 60, t, t + 0.4, False, None)
        t += 0.4
    assert m.loss.updates == before
    for i in range(6):  # ohne Ladesignal 1 K je 5 min wärmer (Signal fehlt): nicht lernen
        m.step(t0 + timedelta(minutes=60 + 5 * i), 5 / 60, t, t + 1.0, False, None)
        t += 1.0
    assert m.loss.updates == before
    for i in range(6):  # 30 min echtes Auskühlen -> ein Lernschritt über den ganzen Abschnitt
        m.step(t0 + timedelta(minutes=95 + 5 * i), 5 / 60, t, t - 0.1, False, None)
        t -= 0.1
    assert m.loss.updates == before + 1


def test_rare_big_draw_does_not_dominate_profile():
    """Wanne alle zwei Wochen um 18 Uhr: getrennt gemerkt, Profil zeigt das Übliche."""
    from datetime import datetime, timedelta

    from heatpilot.dhw import DhwModel

    m = DhwModel()
    m.loss.theta = [0.8, -0.04]
    start = datetime(2026, 10, 1, 0, 0)
    t = 55.0
    for i in range(30 * 12 * 24):
        now = start + timedelta(minutes=5 * i)
        drop = 0.0
        if now.hour == 7 and now.minute == 0:
            drop = 3.0  # Duschen morgens
        if now.hour == 18 and now.minute == 0 and now.day in (19, 26):
            drop = 25.0  # Badewanne
        new = t - drop - 0.01
        m.step(now, 5 / 60, t, new, False, None)
        t = new if new > 40 else 55.0
    assert len(m.big_draws) == 2 and all(k >= 20 for _, k in m.big_draws)  # Duschen zählen nicht dazu
    assert m.daily[18] < m.daily[7]  # Profil: Duschen morgens bleibt sichtbar
    assert m.daily[18] <= 3.0


def test_weekday_profile_blends_in_over_weeks():
    """Sonntags wird morgens gebadet, werktags nicht: nach einigen Wochen sieht die Vorhersage das."""
    from datetime import datetime, timedelta

    from heatpilot.dhw import DhwModel

    m = DhwModel()
    m.loss.theta = [0.8, -0.04]
    t, start = 55.0, datetime(2026, 9, 7, 0, 0)  # Montag
    for i in range(8 * 7 * 24 * 12):
        now = start + timedelta(minutes=5 * i)
        drop = 4.0 if now.weekday() == 6 and now.hour == 9 and now.minute == 0 else 0.0
        new = t - drop - 0.01
        m.step(now, 5 / 60, t, new, False, None)
        t = new if new > 40 else 55.0
    s = m.summary(50.0)
    assert s["profile_week"][6][9] > 2 * s["profile_week"][2][9]  # Sonntag 9 Uhr ≫ Mittwoch 9 Uhr
    assert 0.5 <= s["week_weight"][6] <= 0.7

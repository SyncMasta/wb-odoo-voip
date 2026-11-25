def migrate(cr, version):
    """Switch the team overview on for all companies (new default; call duration unchanged)."""
    # Ob der Schalter bewusst aus war, ist nicht unterscheidbar; bisher gab es keine
    # Kundeninstallation, daher werden alle Firmen eingeschaltet (Entscheidung 25.11.2026).
    cr.execute("UPDATE res_company SET wb_voip_team_aktiv = TRUE WHERE wb_voip_team_aktiv IS NOT TRUE")

from odoo import models


class ResGroups(models.Model):
    _inherit = 'res.groups'

    def write(self, vals):
        # Gruppenseitige Pflege (Benutzerliste, implizierte Gruppen) zählt wie das Benutzerformular
        Benutzer = self.env['res.users']
        if (self.env.su and self.env.context.get('install_mode')) or not {'user_ids', 'implied_ids'} & set(vals):
            return super().write(vals)
        grenze = Benutzer._wb_voip_benutzer_grenze()
        if not grenze:
            return super().write(vals)
        vorher = Benutzer._wb_voip_benutzer_zaehlen()
        ergebnis = super().write(vals)
        Benutzer._wb_voip_grenze_pruefen(vorher, grenze)
        return ergebnis

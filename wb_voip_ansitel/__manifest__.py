{
    'name': 'WB VoIP Ansitel',
    'version': '19.0.1.1.0',
    'category': 'Productivity/VoIP',
    'summary': 'Anbindung der Ansitel-Telefonanlage an WB VoIP',
    'description': """
WB VoIP Ansitel
===============
Verbindung von WB VoIP mit der REST-Schnittstelle einer Ansitel-Telefonanlage:
Zugangsdaten an der Anlage (Token nur verschlüsselt), ein gemeinsamer API-Client und
Schnelltasten für Weiterleitung, Nicht stören und Anrufbeantworter je Benutzer sowie der
Abgleich mit der Anrufliste der Anlage (verpasste und anderswo angenommene Anrufe, Geräte).
""",
    'author': 'WISSEN BERATUNG (Tobias Wissen)',
    'website': 'https://www.wissen-beratung.de',
    'license': 'OPL-1',
    'depends': ['wb_voip'],
    'data': [
        'security/ir.model.access.csv',
        'data/tasten_data.xml',
        'data/ir_cron.xml',
        'views/wb_voip_anlage_views.xml',
        'views/wb_voip_taste_views.xml',
        'views/wb_voip_geraet_views.xml',
        'views/res_users_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'wb_voip_ansitel/static/src/**/*',
        ],
        'web.assets_unit_tests': [
            'wb_voip_ansitel/static/tests/**/*',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}

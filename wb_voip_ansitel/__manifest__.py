{
    'name': 'WB VoIP Ansitel',
    'version': '19.0.1.0.0',
    'category': 'Productivity/VoIP',
    'summary': 'Anbindung der Ansitel-Telefonanlage an WB VoIP',
    'description': """
WB VoIP Ansitel
===============
Verbindung von WB VoIP mit der REST-Schnittstelle einer Ansitel-Telefonanlage:
Zugangsdaten an der Anlage (Token nur verschlüsselt), ein gemeinsamer API-Client und
Schnelltasten für Weiterleitung, Nicht stören und Anrufbeantworter je Benutzer.
""",
    'author': 'WISSEN BERATUNG (Tobias Wissen)',
    'website': 'https://www.wissen-beratung.de',
    'license': 'OPL-1',
    'depends': ['wb_voip'],
    'data': [
        'security/ir.model.access.csv',
        'data/tasten_data.xml',
        'views/wb_voip_anlage_views.xml',
        'views/wb_voip_taste_views.xml',
        'views/res_users_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

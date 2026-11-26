{
    'name': 'WB VoIP CRM',
    'version': '19.0.2.0.1',
    'category': 'Productivity/VoIP',
    'summary': 'Anrufe und Anrufprotokolle am CRM-Vorgang',
    'description': """
WB VoIP CRM
===========
Brücke zwischen WB VoIP und CRM: Anrufe werden dem offenen Vorgang des Kontakts
zugeordnet, am Vorgang erscheinen Anrufe und Anrufprotokolle.
""",
    'images': ['static/description/banner.png', 'static/description/vorgaenge-block.png'],
    'author': 'WISSEN BERATUNG (Tobias Wissen)',
    'website': 'https://www.wissen-beratung.de',
    'license': 'OPL-1',
    'depends': ['wb_voip', 'crm'],
    'data': [
        'views/wb_voip_anruf_views.xml',
        'views/crm_lead_views.xml',
        'views/wb_voip_aktion_views.xml',
        'data/wb_voip_aktion_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'wb_voip_crm/static/src/**/*',
        ],
        'web.assets_unit_tests': [
            'wb_voip_crm/static/tests/**/*',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': True,
}

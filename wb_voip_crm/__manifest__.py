{
    'name': 'WB VoIP CRM',
    'version': '19.0.2.1.0',
    'category': 'Productivity/VoIP',
    'summary': 'Calls and call logs on the CRM opportunity',
    'description': """
WB VoIP CRM
===========
Bridge between WB VoIP and CRM: calls are assigned to the contact's open opportunity;
the opportunity shows its calls and call logs.
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

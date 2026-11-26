{
    'name': 'WB VoIP Ansitel',
    'version': '19.0.2.0.1',
    'category': 'Productivity/VoIP',
    'summary': 'Quick keys and call list reconciliation for Ansitel PBXs',
    'description': """
WB VoIP Ansitel
===============
Connects WB VoIP to the REST interface of an Ansitel PBX: access data on the PBX
(token stored encrypted only), a shared API client, quick keys per user for call forwarding,
do not disturb and voicemail, and the reconciliation with the PBX call list (missed calls,
calls answered elsewhere, devices).
""",
    'images': ['static/description/banner.png', 'static/description/tastenleiste.png'],
    'author': 'WISSEN BERATUNG (Tobias Wissen)',
    'website': 'https://www.wissen-beratung.de',
    'license': 'OPL-1',
    'depends': ['wb_voip'],
    'external_dependencies': {'python': ['phonenumbers']},
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

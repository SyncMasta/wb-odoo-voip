{
    'name': 'WB VoIP',
    'version': '19.0.5.2.0',
    'category': 'Productivity/VoIP',
    'summary': 'Softphone, call log and PBX integration for Odoo',
    'description': """
WB VoIP
=======
Browser softphone, call log and integration with SIP PBXs.
""",
    'images': ['static/description/banner.png', 'static/description/softphone-waehlen.png', 'static/description/protokoll-popup.png', 'static/description/softphone-verpasst.png', 'static/description/softphone-team.png', 'static/description/designer.png'],
    'author': 'WISSEN BERATUNG (Tobias Wissen)',
    'website': 'https://www.wissen-beratung.de',
    'license': 'OPL-1',
    'depends': ['mail', 'phone_validation', 'bus', 'web', 'wb_license_client'],
    'external_dependencies': {'python': ['phonenumbers']},
    'excludes': ['voip'],
    'data': [
        'security/wb_voip_security.xml',
        'security/ir.model.access.csv',
        'views/wb_voip_anlage_views.xml',
        'wizards/wb_voip_aktion_assistent_views.xml',
        'views/wb_voip_aktion_views.xml',
        'views/wb_voip_layout_views.xml',
        'views/res_users_views.xml',
        'views/wb_voip_anruf_views.xml',
        'views/wb_voip_protokoll_views.xml',
        'wizards/wb_voip_zuordnen_views.xml',
        'views/res_partner_views.xml',
        'data/ir_config_parameter.xml',
        'data/ir_cron.xml',
        'data/wb_voip_aktion_data.xml',
        'data/wb_voip_layout_data.xml',
        'data/mail_activity_type_data.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'wb_voip/static/src/**/*',
        ],
        # SIP.js nur bei Bedarf (Anlage im Modus „prod“) per loadBundle
        'wb_voip.sip_lib': [
            'wb_voip/static/lib/sip-0.21.2.min.js',
        ],
        'web.assets_unit_tests': [
            'wb_voip/static/tests/**/*',
            ('remove', 'wb_voip/static/tests/tours/**/*'),
        ],
        'web.assets_tests': [
            'wb_voip/static/tests/tours/**/*',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    'post_init_hook': '_wb_voip_post_init',
    'uninstall_hook': '_wb_voip_uninstall',
}

import os
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo.tests import TransactionCase


class WbVoipCase(TransactionCase):
    """Base class for WB VoIP tests: users, license patches, Fernet key."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Fernet-Schlüssel wie im Lizenz-Leitfaden §6, danach zurückstellen
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = Fernet.generate_key().decode('utf-8')
        cls.addClassCleanup(cls._fernet_env_zurueck)
        # Eigener Schlüssel für das SIP-Passwort je Testklasse; der Schlüssel der Instanz
        # bleibt unberührt und wird nie ausgegeben.
        cls.voip_schluessel = Fernet.generate_key().decode('utf-8')
        voip_patch = patch.dict(os.environ, {'WB_VOIP_FERNET_KEY': cls.voip_schluessel})
        voip_patch.start()
        cls.addClassCleanup(voip_patch.stop)

        gruppe_benutzer = cls.env.ref('wb_voip.group_wb_voip_user')
        gruppe_manager = cls.env.ref('wb_voip.group_wb_voip_manager')
        # A und B dürfen Kontakte ändern (wie Mitarbeitende im Standard), damit die
        # Chatter-Notiz am Kontakt möglich ist (verlangt Schreibrecht).
        # Gespeicherte Texte entstehen in der Sprache des Benutzers: die Tests laufen auf Englisch
        cls.env['res.lang']._activate_lang('en_US')
        cls.env.company.partner_id.lang = 'en_US'
        Users = cls.env['res.users'].with_context(no_reset_password=True, lang='en_US')
        cls.user_a = Users.create({
            'name': 'VoIP Benutzer A', 'login': 'wb_voip_a', 'lang': 'en_US',
            'email': 'wb_voip_a@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, gruppe_benutzer.id,
                                  cls.env.ref('base.group_partner_manager').id])],
        })
        cls.anlage = cls.env['wb.voip.anlage'].create({
            'name': 'Testanlage', 'modus': 'test',
            'ws_url': 'wss://anlage.test/ws', 'sip_domain': 'anlage.test',
        })
        cls.user_a.write({
            'wb_voip_anlage_id': cls.anlage.id,
            'wb_voip_sip_benutzer': '100',
            'wb_voip_sip_passwort': 'geheim',
        })
        cls.user_b = Users.create({
            'name': 'VoIP Benutzer B', 'login': 'wb_voip_b', 'lang': 'en_US',
            'email': 'wb_voip_b@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, gruppe_benutzer.id,
                                  cls.env.ref('base.group_partner_manager').id])],
        })
        cls.user_manager = Users.create({
            'name': 'VoIP Manager', 'login': 'wb_voip_manager', 'lang': 'en_US',
            'email': 'wb_voip_manager@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, gruppe_manager.id])],
        })

    @classmethod
    def _fernet_env_zurueck(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup

    def _lizenz_stellen(self, gueltig):
        """Patch check_license so that license_required sees (in)valid info."""
        info = SimpleNamespace(
            is_valid=gueltig, max_users=0,
            user_message='' if gueltig else 'Lizenz fehlt oder abgelaufen',
        )
        patcher = patch.object(
            type(self.env['wb.license.client']), 'check_license',
            return_value=info,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _lizenz_gueltig(self):
        """Make license checks succeed."""
        self._lizenz_stellen(True)

    def _lizenz_fehlt(self):
        """Make license checks fail (license_required raises UserError)."""
        self._lizenz_stellen(False)

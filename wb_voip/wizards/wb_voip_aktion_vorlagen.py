"""Delivered templates of the button wizard (code data, no user Python).

Each template is a fixed "set field" on the contact: model, field and which wizard
field holds the chosen value. Templates never create anything but ``object_write``.
"""

VORLAGEN = {
    'kategorie_hinzufuegen': {
        'name': 'Kategorie am Kontakt hinzufügen',
        'modell': 'res.partner',
        'feld': 'category_id',
        'wert': 'wert_kategorie_id',
    },
    'sprache_setzen': {
        'name': 'Sprache des Kontakts setzen',
        'modell': 'res.partner',
        'feld': 'lang',
        'wert': 'wert_sprache',
    },
}

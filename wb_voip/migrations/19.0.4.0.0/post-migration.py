def migrate(cr, version):
    """Set the company of existing calls to the company of their user."""
    cr.execute("""
        UPDATE wb_voip_anruf a
           SET company_id = u.company_id
          FROM res_users u
         WHERE a.user_id = u.id
    """)

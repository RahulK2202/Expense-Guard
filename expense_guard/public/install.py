import frappe


def after_install():
    """
    Runs once automatically after:
        bench --site your.site install-app expense_guard

    Creates the custom Role so permissions work correctly.
    """
    _create_role()
    frappe.db.commit()
    print("✅  Expense Guard installed successfully.")


def _create_role():
    if frappe.db.exists("Role", "Expense Policy Manager"):
        print("   → Role already exists, skipping.")
        return

    role = frappe.new_doc("Role")
    role.role_name   = "Expense Policy Manager"
    role.desk_access = 1
    role.insert(ignore_permissions=True)
    print("   → Role 'Expense Policy Manager' created.")
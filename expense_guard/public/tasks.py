import frappe
from frappe import _
from frappe.utils import add_months, today, getdate


def monthly_policy_violation_report():
    """
    Runs on the 1st of every month automatically.
    Sends a violation summary email to all Expense Policy Managers.
    """
    month_start = getdate(add_months(today(), -1)).replace(day=1)
    month_end   = getdate(today()).replace(day=1)

    logs = frappe.get_all(
        "Expense Violation Log",
        filters={
            "creation": ["between", [str(month_start), str(month_end)]]
        },
        fields=[
            "employee", "expense_type",
            "claimed_amount", "policy_limit", "action_taken",
        ],
        order_by="creation desc",
    )

    if not logs:
        return   # Nothing to report this month

    # Build HTML email table
    rows = "".join(
        f"<tr>"
        f"<td>{l.employee}</td>"
        f"<td>{l.expense_type}</td>"
        f"<td>₹{l.claimed_amount:,.0f}</td>"
        f"<td>₹{l.policy_limit:,.0f}</td>"
        f"<td><b>{l.action_taken}</b></td>"
        f"</tr>"
        for l in logs
    )

    html = f"""
    <p>Policy violations for <b>{month_start.strftime('%B %Y')}</b>:</p>
    <table border="1" cellpadding="6" cellspacing="0"
           style="border-collapse:collapse; width:100%">
        <thead style="background:#f4f4f4">
            <tr>
                <th>Employee</th>
                <th>Expense Type</th>
                <th>Claimed</th>
                <th>Limit</th>
                <th>Action</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>
    <p>Total violations: <b>{len(logs)}</b></p>
    """

    # Get all Expense Policy Managers to email
    recipients = frappe.get_all(
        "Has Role",
        filters={"role": "Expense Policy Manager", "parenttype": "User"},
        pluck="parent",
    )

    if not recipients:
        return

    frappe.sendmail(
        recipients=recipients,
        subject=_("[Expense Guard] Monthly Violation Report — {0}").format(
            month_start.strftime("%B %Y")
        ),
        message=html,
        now=True,
    )
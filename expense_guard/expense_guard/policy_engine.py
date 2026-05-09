import frappe
from frappe import _
from frappe.utils import flt


# ─────────────────────────────────────────────
# HOOK FUNCTIONS (called from hooks.py)
# ─────────────────────────────────────────────

def validate_expense_claim(doc, method=None):
    """
    Runs every time an Expense Claim is saved.
    This is the main enforcement gate.
    """
    policy = _get_applicable_policy(doc.employee)

    # If no policy exists for this employee, do nothing
    if not policy:
        return

    rule_map   = _build_rule_map(policy)
    violations = _collect_violations(doc, rule_map)
    _enforce(violations)


def log_violations_on_submit(doc, method=None):
    """
    Runs when Expense Claim is submitted.
    Warn-level violations that passed through get recorded
    in Expense Violation Log for Finance audit.
    """
    policy = _get_applicable_policy(doc.employee)
    if not policy:
        return

    rule_map   = _build_rule_map(policy)
    violations = _collect_violations(doc, rule_map)

    for v in violations:
        if v["action"] == "Warn":
            _create_violation_log(doc, v, policy.name)


# ─────────────────────────────────────────────
# WHITELISTED API (called from JS)
# ─────────────────────────────────────────────

@frappe.whitelist()
def get_policy_rule(employee, expense_type):
    """
    Called by client JS to get the rule for a specific
    employee + expense type combination.
    Used for real-time alerts while user is typing.
    """
    frappe.has_permission("Expense Claim", throw=True)

    policy = _get_applicable_policy(employee)
    if not policy:
        return None

    rule_map = _build_rule_map(policy)
    rule = rule_map.get(expense_type)
    print(rule,"this is the rulessss")
    if not rule:
        return None

    return {
        "max_amount":          flt(rule.max_amount),
        "action_on_violation": rule.action,
        "receipt_mandatory":   rule.receipt_mandatory,
        "period":              rule.period,
    }


# ─────────────────────────────────────────────
# INTERNAL HELPERS (private — not called outside)
# ─────────────────────────────────────────────

def _get_applicable_policy(employee):
    """
    Finds the most specific active policy for this employee.

    Priority chain:
        1. Employee-specific policy     (most specific)
        2. Designation-level policy
        3. Department-level policy
        4. All Employees policy         (most general)

    Returns the first match found, or None.
    """
    if not employee:
        return None

    # get_cached_doc avoids hitting DB again if already loaded
    emp = frappe.get_cached_doc("Employee", employee)

    # Walk the priority chain
    candidates = [
        {"applies_to": "Employee",      "employee":    employee},
        {"applies_to": "Designation",   "designation": emp.designation},
        {"applies_to": "Department",    "department":  emp.department},
        {"applies_to": "All Employees"},
    ]

    for filters in candidates:
        results = frappe.get_all(
            "Expense Policy",
            filters={"active": 1, **filters},
            fields=["name"],
            limit=1,
            order_by="modified desc",
        )
        if results:
            return frappe.get_doc("Expense Policy", results[0].name)

    return None


def _build_rule_map(policy):
    """
    Converts the child table rows into a simple dictionary:
        { "Meals": <rule row>, "Hotel": <rule row> }

    This makes lookup O(1) instead of looping every time.
    """
    return {row.expense_type: row for row in policy.expense_policy_rules}


def _collect_violations(doc, rule_map):
    """
    Loops through every expense row and checks it against the rule.
    Returns a list of violation dicts.
    """
    violations = []

    # Track daily totals across rows in the same claim
    # Key: (expense_type, date) → total amount so far
    daily_totals = {}

    for row in doc.expenses:
        rule = rule_map.get(row.expense_type)

        # No rule for this expense type → skip
        if not rule:
            continue

        amount  = flt(row.amount)
        max_amt = flt(rule.max_amount)

        # ── Check 1: Per Claim limit ──────────────────
        if rule.period == "Per Claim" and amount > max_amt:
            violations.append({
                "row":     row,
                "rule":    rule,
                "action":  rule.action,
                "message": _(
                    "Row #{0}: <b>{1}</b> — ₹{2} exceeds "
                    "the per-claim limit of ₹{3}."
                ).format(
                    row.idx,
                    row.expense_type,
                    f"{amount:,.0f}",
                    f"{max_amt:,.0f}",
                )
            })

        # ── Check 2: Per Day limit ────────────────────
        elif rule.period == "Per Day":
            key = (row.expense_type, str(row.expense_date))
            daily_totals[key] = daily_totals.get(key, 0) + amount

            # Also include already-submitted claims for same day
            already_submitted = flt(
                frappe.db.get_value(
                    "Expense Claim Detail",
                    {
                        "expense_type": row.expense_type,
                        "expense_date": row.expense_date,
                        "docstatus":    1,
                        "parent":       ["!=", doc.name],
                    },
                    "sum(amount)",
                ) or 0
            )

            day_total = daily_totals[key] + already_submitted

            if day_total > max_amt:
                violations.append({
                    "row":     row,
                    "rule":    rule,
                    "action":  rule.action,
                    "message": _(
                        "Row #{0}: <b>{1}</b> on {2} — "
                        "daily total ₹{3} exceeds per-day limit of ₹{4}."
                    ).format(
                        row.idx,
                        row.expense_type,
                        row.expense_date,
                        f"{day_total:,.0f}",
                        f"{max_amt:,.0f}",
                    )
                })

        # ── Check 3: Receipt mandatory ────────────────
        if rule.receipt_mandatory and not _has_attachment(doc):
            violations.append({
                "row":     row,
                "rule":    rule,
                "action":  "Block",   # always block — no receipt = no pass
                "message": _(
                    "Row #{0}: <b>{1}</b> — "
                    "a receipt attachment is mandatory."
                ).format(row.idx, row.expense_type)
            })

    return violations


def _enforce(violations):
    """
    Applies the action for each violation:
        Warn  → frappe.msgprint (orange, saves anyway)
        Block → frappe.throw   (red, stops save completely)
    """
    warns  = [v for v in violations if v["action"] == "Warn"]
    blocks = [v for v in violations if v["action"] == "Block"]

    # Show all warnings first (user sees them even if blocked)
    for v in warns:
        frappe.msgprint(
            v["message"],
            title=_("Expense Policy Warning"),
            indicator="orange",
        )

    # Then throw on blocks — this stops execution immediately
    if blocks:
        block_messages = "<br><br>".join(v["message"] for v in blocks)
        frappe.throw(
            _("Resolve these violations before saving:"
              "<br><br>{0}").format(block_messages),
            title=_("Expense Policy Violation"),
            exc=frappe.ValidationError,
        )


def _has_attachment(doc):
    """
    Checks if any file is attached to this Expense Claim.
    Frappe stores all attachments in the File doctype.
    """
    return frappe.db.count(
        "File",
        {
            "attached_to_doctype": "Expense Claim",
            "attached_to_name":    doc.name,
        }
    ) > 0


def _create_violation_log(doc, violation, policy_name):
    """
    Creates an Expense Violation Log entry.
    Called on submit for Warn-level violations only.
    """
    try:
        log = frappe.new_doc("Expense Violation Log")
        log.update({
            "expense_claim":  doc.name,
            "employee":       doc.employee,
            "expense_type":   violation["row"].expense_type,
            "claimed_amount": flt(violation["row"].amount),
            "policy_limit":   flt(violation["rule"].max_amount),
            "violation_date": violation["row"].expense_date,
            "action_taken":   "Warned",
            "policy":         policy_name,
            "message":        violation["message"],
        })
        log.insert(ignore_permissions=True)

    except Exception:
        # Never crash the main submit flow because of logging
        frappe.log_error(
            frappe.get_traceback(),
            "Expense Guard: Failed to create violation log"
        )
frappe.provide("expense_guard");

// ── Runs when Expense Claim form loads ──────────────────
frappe.ui.form.on("Expense Claim", {
    refresh(frm) {
        expense_guard.show_policy_badge(frm);
    },

    // Re-check when employee changes
    employee(frm) {
        frm._eg_cache = {};
        expense_guard.show_policy_badge(frm);
    },
});

// ── Runs when user changes anything in expense rows ─────
frappe.ui.form.on("Expense Claim Detail", {
    expense_type(frm, cdt, cdn) {
        expense_guard.check_row(frm, cdt, cdn);
    },
    amount(frm, cdt, cdn) {
        expense_guard.check_row(frm, cdt, cdn);
    },
    expense_date(frm, cdt, cdn) {
        expense_guard.check_row(frm, cdt, cdn);
    },
});

// ── Helper functions ────────────────────────────────────
$.extend(expense_guard, {

    // Shows a blue badge at top of form so user knows policy is active
    show_policy_badge(frm) {
        frm.dashboard.clear_comment();
        if (!frm.doc.employee) return;

        frm.dashboard.add_comment(
            __("🛡️ Expense Guard active — limits enforced on save."),
            "blue",
            true
        );
    },

    // Checks a single row against the policy in real time
    check_row(frm, cdt, cdn) {
        const row = locals[cdt][cdn];

        // Need all three fields before we can check
        if (!frm.doc.employee || !row.expense_type || !row.amount) return;

        // Cache rules so we don't call server on every keystroke
        frm._eg_cache = frm._eg_cache || {};
        const cache_key = row.expense_type;

        const show_alert = (rule) => {
            if (!rule) return;

            const amount    = flt(row.amount);
            const limit     = flt(rule.max_amount);
            const exceeded  = amount > limit;

            if (!exceeded) return;

            const msg = __(
                "Row #{0}: {1} of ₹{2} exceeds the {3} limit of ₹{4}.",
                [
                    row.idx,
                    row.expense_type,
                    format_number(amount, null, 0),
                    rule.period.toLowerCase(),
                    format_number(limit, null, 0),
                ]
            );

            // Red for Block, Orange for Warn
            const color = rule.action_on_violation === "Block" ? "red" : "orange";
            frappe.show_alert({ message: msg, indicator: color }, 6);
        };

        // Use cached rule if available
        if (frm._eg_cache[cache_key] !== undefined) {
            show_alert(frm._eg_cache[cache_key]);
            return;
        }

        // Call the whitelisted Python method
        frappe.call({
            method: "expense_guard.expense_guard.policy_engine.get_policy_rule",
            args: {
                employee:     frm.doc.employee,
                expense_type: row.expense_type,
            },
            callback(r) {
                // Store in cache for this session
                frm._eg_cache[cache_key] = r.message || null;
                show_alert(r.message);
            },
        });
    },
});
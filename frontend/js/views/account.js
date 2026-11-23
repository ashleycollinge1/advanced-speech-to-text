import { api } from "../api.js";
import { h, reveal, when, toast, busy, formError } from "../dom.js";

export function render(main, ctx) {
  const u = ctx.session.user;
  const head = h("header.page-head",
    h("p.eyebrow", "Account"),
    h("h1.display", h("span.thin", "Hello, "), u.username, h("span.accent", ".")));

  const info = h("section.panel.panel-pad", { "aria-labelledby": "acc-h" },
    h("h2#acc-h", { style: { "margin-bottom": "1.25rem" } }, "Details"),
    h("dl.kv",
      h("dt", "Username"), h("dd", u.username),
      h("dt", "Role"), h("dd", u.role === "admin" ? "Administrator" : "User"),
      h("dt", "Member since"), h("dd", when(u.created_at)),
      h("dt", "Version"), h("dd.mono", ctx.session.version || "dev")),
    h("div", { style: { height: "1.5rem" } }),
    h("button.btn", { type: "button", onclick: ctx.signOut }, "Sign out"));

  const err = h("div", { "aria-live": "assertive" });
  const submit = h("button.btn.btn-primary", { type: "submit", dataset: { busyLabel: "Saving…" } }, "Change password");
  const pw = (id, label, ac, hint) => h("div.field", h("label", { for: id }, label),
    h(`input.input#${id}`, { type: "password", name: id, required: true, autocomplete: ac }), hint && h("p.hint", hint));
  const form = h("form.form.panel.panel-pad", { novalidate: true, "aria-labelledby": "pw-h" },
    h("h2#pw-h", "Change password"),
    err,
    pw("current", "Current password", "current-password"),
    pw("next", "New password", "new-password", "At least 12 characters. Other devices will be signed out."),
    pw("confirm", "Confirm new password", "new-password"),
    submit);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.replaceChildren();
    if (!form.current.value || !form.next.value) return err.append(formError("Fill in all fields."));
    if (form.next.value !== form.confirm.value) return err.append(formError("The new passwords don't match."));
    await busy(submit, async () => {
      try {
        await api("/api/auth/password", { method: "POST", body: { current_password: form.current.value, new_password: form.next.value } });
        form.reset();
        toast("Password changed. Other sessions were signed out.");
      } catch (ex) {
        err.append(formError(ex.message));
      }
    });
  });

  reveal(head, info, form);
  main.append(h("div.page", head, h("div.account-grid", info, form)));
}

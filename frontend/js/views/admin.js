import { api } from "../api.js";
import { h, clear, icon, reveal, when, toast, busy, dialog, confirmDialog, formError, copyText } from "../dom.js";

function showTempPassword(username, password, created) {
  return dialog({
    title: created ? `${username} created` : `Password reset for ${username}`,
    body: h("div.form",
      h("p.muted", "Share this temporary password securely. It's shown only once. They'll choose their own password at first sign-in."),
      h("div.secret", h("span", password),
        h("button.btn.btn-sm", { type: "button", onclick: () => copyText(password) }, icon("copy"), "Copy"))),
    actions: [{ label: "Done", value: true, primary: true }],
  });
}

export function render(main, ctx) {
  const me = ctx.session.user;
  let users = null;
  let disposed = false;

  const head = h("header.page-head",
    h("p.eyebrow", "Administration"),
    h("h1.display", h("span.thin", "People "), "& access"));

  // ----------------------------------------------------------------- create form
  const err = h("div", { "aria-live": "assertive" });
  const submit = h("button.btn.btn-primary", { type: "submit", dataset: { busyLabel: "Creating…" } }, "Create user");
  const form = h("form.form.panel.panel-pad", { novalidate: true, "aria-labelledby": "new-h" },
    h("h2#new-h", "Add a user"),
    h("p.muted", "A temporary password is generated for you to pass on."),
    err,
    h("div.field", h("label", { for: "nu-name" }, "Username"),
      h("input.input#nu-name", { name: "username", required: true, autocapitalize: "none", spellcheck: "false", autocomplete: "off", maxlength: 64 })),
    h("div.field", h("label", { for: "nu-role" }, "Role"),
      h("select.select#nu-role", { name: "role" },
        h("option", { value: "user" }, "User: sees only their own recordings"),
        h("option", { value: "admin" }, "Admin: manages users, sees everything"))),
    submit);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.replaceChildren();
    const username = form.username.value.trim();
    if (!username) {
      form.username.setAttribute("aria-invalid", "true");
      return err.append(formError("Enter a username."));
    }
    form.username.removeAttribute("aria-invalid");
    await busy(submit, async () => {
      try {
        const res = await api("/api/users", { method: "POST", body: { username, role: form.role.value } });
        form.reset();
        users = [...(users || []), res.user].sort((a, b) => a.username.localeCompare(b.username));
        renderTable();
        await showTempPassword(res.user.username, res.temporary_password, true);
      } catch (ex) {
        err.append(formError(ex.message));
      }
    });
  });

  // ----------------------------------------------------------------- table
  const tableHost = h("div");
  const listSection = h("section", { "aria-labelledby": "users-h" }, h("h2#users-h", { style: { "margin-bottom": "1rem" } }, "Users"), tableHost);

  async function act(fn, okMsg) {
    try {
      await fn();
      if (okMsg) toast(okMsg);
    } catch (ex) {
      toast(ex.message, "error");
    }
  }

  function row(u) {
    const self = u.id === me.id;
    const tools = [];
    if (!self) {
      tools.push(
        h("button.btn.btn-sm.btn-ghost", { type: "button", title: u.role === "admin" ? "Make regular user" : "Make admin",
          onclick: () => act(async () => {
            const res = await api(`/api/users/${u.id}`, { method: "PATCH", body: { role: u.role === "admin" ? "user" : "admin" } });
            Object.assign(u, res.user);
            renderTable();
          }, "Role updated") }, u.role === "admin" ? "Demote" : "Make admin"),
        h("button.btn.btn-sm.btn-ghost", { type: "button",
          onclick: () => act(async () => {
            const res = await api(`/api/users/${u.id}`, { method: "PATCH", body: { is_active: !u.is_active } });
            Object.assign(u, res.user);
            renderTable();
          }, u.is_active ? "User deactivated" : "User reactivated") }, u.is_active ? "Deactivate" : "Reactivate"),
        h("button.btn.btn-sm.btn-ghost", { type: "button",
          onclick: async () => {
            if (!(await confirmDialog(`Reset ${u.username}'s password?`, "They'll be signed out everywhere and must set a new password at next sign-in.", "Reset password"))) return;
            act(async () => {
              const res = await api(`/api/users/${u.id}/reset-password`, { method: "POST" });
              Object.assign(u, res.user);
              renderTable();
              await showTempPassword(u.username, res.temporary_password, false);
            });
          } }, "Reset password"),
        h("button.btn.btn-sm.btn-icon.btn-danger", { type: "button", title: "Delete user", "aria-label": `Delete ${u.username}`,
          onclick: async () => {
            if (!(await confirmDialog(`Delete ${u.username}?`, `This permanently deletes their account and all ${u.recordings} recording(s), including audio and transcripts.`))) return;
            act(async () => {
              await api(`/api/users/${u.id}`, { method: "DELETE" });
              users = users.filter((x) => x.id !== u.id);
              renderTable();
            }, "User deleted");
          } }, icon("trash")),
      );
    }
    return h("tr",
      h("td", h("span.user-name", u.username), self && h("span.faint", " (you)")),
      h("td", h(`span.badge${u.role === "admin" ? ".badge-admin" : ""}`, u.role),
        !u.is_active && h("span.badge.badge-off", { style: { "margin-left": ".35rem" } }, "inactive"),
        u.must_change_password && u.is_active && h("span.badge", { style: { "margin-left": ".35rem" } }, "pending")),
      h("td.mono", String(u.recordings ?? 0)),
      h("td.faint", u.last_login_at ? when(u.last_login_at) : "Never"),
      h("td.actions", tools));
  }

  function renderTable() {
    clear(tableHost);
    if (!users) {
      tableHost.append(h("div", { "aria-busy": "true" }, [0, 1, 2].map(() => h("div.skeleton-row", h("span.skeleton.skeleton-line.w-55")))));
      return;
    }
    if (users.length <= 1) {
      tableHost.append(h("div.empty",
        h("p.empty-mark", { "aria-hidden": "true" }, "1"),
        h("h3", "Just you so far"),
        h("p", "Add people with the form. Each user gets a private library.")));
    }
    tableHost.append(h("div.table-wrap", h("table",
      h("thead", h("tr", ["User", "Role", "Recordings", "Last sign-in", h("span.visually-hidden", "Actions")].map((t) => h("th", { scope: "col" }, t)))),
      h("tbody", users.map(row)))));
  }

  async function load() {
    try {
      users = (await api("/api/users")).users;
      if (!disposed) renderTable();
    } catch (ex) {
      if (disposed) return;
      clear(tableHost).append(h("div.error-state", { role: "alert" },
        h("h2", "Couldn't load users"), h("p", ex.message),
        h("button.btn", { type: "button", onclick: () => { users = null; renderTable(); load(); } }, icon("retry"), "Retry")));
    }
  }

  reveal(head, listSection, form);
  main.append(h("div.page", head, h("div.two-col", listSection, form)));
  renderTable();
  load();
  return () => { disposed = true; };
}

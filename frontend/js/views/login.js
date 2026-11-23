import { api } from "../api.js";
import { h, reveal, formError, busy, toast, autofocus } from "../dom.js";

function wave() {
  // Decorative, deterministic waveform.
  const bars = [];
  for (let i = 0; i < 48; i++) {
    const v = 0.25 + 0.75 * Math.abs(Math.sin(i * 0.55) * Math.cos(i * 0.17));
    bars.push(h("span", { style: { "--h": `${Math.round(v * 100)}%` } }));
  }
  return h("div.wave", { "aria-hidden": "true" }, bars);
}

function hero() {
  const eyebrow = h("p.eyebrow", "Speech → Text");
  const title = h("h1", h("span.thin", "Every word,"), h("br"), "on the ", h("span.accent", "record."));
  const lede = h("p.lede", "Turn recordings into searchable, playable transcripts. Highlight any line to hear exactly how it was said.");
  const w = wave();
  reveal(eyebrow, title, lede, w);
  return h("section.auth-hero.grid-texture", h("div", eyebrow, h("div", { style: { height: "1.5rem" } }), title), h("div", lede, h("div", { style: { height: "2rem" } }), w));
}

function passwordField(id, label, autocomplete, hint) {
  return h("div.field",
    h("label", { for: id }, label),
    h(`input.input#${id}`, { type: "password", name: id, required: true, autocomplete, "aria-describedby": hint ? `${id}-hint` : null }),
    hint && h(`p.hint#${id}-hint`, hint));
}

export function render(main, ctx) {
  const wrap = h("div.auth");
  const formSide = h("section.auth-form-wrap");
  wrap.append(hero(), formSide);
  main.append(wrap);
  return ctx.mode === "password" ? renderPassword(formSide, ctx) : renderLogin(formSide, ctx);
}

function renderLogin(host, ctx) {
  const err = h("div", { "aria-live": "assertive" });
  const submit = h("button.btn.btn-primary", { type: "submit", dataset: { busyLabel: "Signing in…" } }, "Sign in");
  const form = h("form.form", { novalidate: true },
    h("div", h("h2", "Sign in"), h("p.muted", "Use the account your administrator created.")),
    err,
    h("div.field", h("label", { for: "username" }, "Username"),
      h("input.input#username", { name: "username", autocomplete: "username", required: true, autocapitalize: "none", spellcheck: "false" })),
    passwordField("password", "Password", "current-password"),
    submit);
  reveal(form);
  host.append(form);
  autofocus(form.username);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.replaceChildren();
    const username = form.username.value.trim();
    const password = form.password.value;
    form.username.setAttribute("aria-invalid", String(!username));
    form.password.setAttribute("aria-invalid", String(!password));
    if (!username || !password) {
      err.append(formError("Enter your username and password."));
      return;
    }
    await busy(submit, async () => {
      try {
        const res = await api("/api/auth/login", { method: "POST", body: { username, password } });
        ctx.setSession(res.user, res.csrf_token);
        ctx.navigate(res.user.must_change_password ? "/password" : "/");
      } catch (ex) {
        err.append(formError(ex.message));
        form.password.value = "";
        form.password.focus();
      }
    });
  });
}

function renderPassword(host, ctx) {
  const err = h("div", { "aria-live": "assertive" });
  const submit = h("button.btn.btn-primary", { type: "submit", dataset: { busyLabel: "Saving…" } }, "Set password & continue");
  const form = h("form.form", { novalidate: true },
    h("div", h("h2", "Choose a new password"),
      h("p.muted", `Signed in as ${ctx.session.user.username}. You need to replace your temporary password before continuing.`)),
    err,
    passwordField("current", "Temporary password", "current-password"),
    passwordField("next", "New password", "new-password", "At least 12 characters. A passphrase of a few random words works well."),
    passwordField("confirm", "Confirm new password", "new-password"),
    submit,
    h("button.btn.btn-ghost", { type: "button", onclick: ctx.signOut }, "Sign out instead"));
  reveal(form);
  host.append(form);
  autofocus(form.current);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.replaceChildren();
    const current = form.current.value;
    const next = form.next.value;
    if (!current || !next) return err.append(formError("Fill in all fields."));
    if (next !== form.confirm.value) {
      form.confirm.setAttribute("aria-invalid", "true");
      return err.append(formError("The new passwords don't match."));
    }
    await busy(submit, async () => {
      try {
        const res = await api("/api/auth/password", { method: "POST", body: { current_password: current, new_password: next } });
        ctx.setSession(res.user);
        toast("Password updated. Welcome!");
        ctx.navigate("/");
      } catch (ex) {
        err.append(formError(ex.message));
      }
    });
  });
}

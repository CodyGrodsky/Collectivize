"use strict";

// The sign-in form, shared by the landing page and the invite page. The
// page provides <form id="signin"> with #signin-email, #signin-button and
// #signin-message; this adds the later steps under the email row.
//
//   1. Email typed            -> POST /api/signin
//   2a. An ordinary address   -> a 6-digit code is emailed; typing it back
//                                (POST /api/signin/verify) signs you in.
//   2b. An admin account      -> its password (POST /api/admin/signin), then
//                                Duo: a push to the phone, or a passcode.
//
// new SignInForm({ extra: () => ({invite}), done: () => location.replace(...) })

(() => {
    const { Api, Ui } = window.Collectivize;
    const { el } = Ui;
    const $ = (id) => document.getElementById(id);

    class SignInForm {
        constructor({ extra = () => ({}), done, adminDone = () => location.replace("admin") }) {
            this.extra = extra;
            this.done = done;
            this.adminDone = adminDone;
            this.step = "email"; // "email", "code" or "admin"
            this.stepFor = ""; // the address the current step was started for
            this.buttonLabel = $("signin-button").textContent;
            this.build();
            $("signin").addEventListener("submit", (event) => {
                event.preventDefault();
                this.submit();
            });
            // A different address typed: start over. Only when it really is
            // different: a password manager filling in the saved name and
            // password fires this too, and must not wipe the password step.
            $("signin-email").addEventListener("input", () => {
                if (this.typed === this.stepFor || this.step === "email") return;
                this.setStep("email");
                this.say(""); // the prompt was about the other address
            });
        }

        build() {
            const field = (id, label, attrs) => [
                el("label", { className: "signin-label", text: label, attrs: { for: id } }),
                el("input", { className: "signin-input", attrs: { id, "aria-describedby": "signin-message", ...attrs } }),
            ];
            this.resend = el("button", { className: "signin-link", text: "Send a new code", attrs: { type: "button" } });
            this.resend.addEventListener("click", () => this.sendCode(true));
            this.codeBox = el("div", { className: "signin-extra", attrs: { hidden: true } }, [
                ...field("signin-code", "Code from your email", {
                    inputmode: "numeric", autocomplete: "one-time-code", maxlength: "10", placeholder: "6 digits",
                }),
                this.resend,
            ]);
            this.duoBox = el("div", { attrs: { hidden: true } }, field("signin-passcode", "Duo passcode", {
                inputmode: "numeric", autocomplete: "one-time-code", maxlength: "20", placeholder: "from the Duo Mobile app",
            }));
            this.adminBox = el("div", { className: "signin-extra", attrs: { hidden: true } }, [
                ...field("signin-password", "Admin password", {
                    type: "password", autocomplete: "current-password", maxlength: "200",
                }),
                this.duoBox,
            ]);
            $("signin-message").before(this.codeBox, this.adminBox);
        }

        setStep(step) {
            this.stepFor = step === "email" ? "" : this.typed;
            if (step === this.step) return;
            this.step = step;
            this.codeBox.hidden = step !== "code";
            this.adminBox.hidden = step !== "admin";
            $("signin-button").textContent = step === "code" ? "Verify" : this.buttonLabel;
            if (step === "email") {
                for (const id of ["signin-code", "signin-password", "signin-passcode"]) $(id).value = "";
                this.duoBox.hidden = true;
            }
        }

        /** `info`: a prompt for the next step, not an error (so not red). */
        say(text, { info = false } = {}) {
            $("signin-message").textContent = text;
            $("signin-message").classList.toggle("is-info", info);
        }

        get typed() {
            return $("signin-email").value.trim();
        }

        async submit() {
            if (!this.typed) {
                this.say("Enter your Northeastern email.");
                return;
            }
            $("signin-button").disabled = true;
            this.say("");
            try {
                if (this.step === "code") await this.verify();
                else if (this.step === "admin") await this.adminSignIn();
                else await this.sendCode(false);
            } catch (error) {
                this.say(error.message);
                if (error.data?.needs_passcode) {
                    this.duoBox.hidden = false;
                    $("signin-passcode").focus();
                } else {
                    $({ code: "signin-code", admin: "signin-password", email: "signin-email" }[this.step]).focus();
                }
            } finally {
                $("signin-button").disabled = false;
            }
        }

        /** Step 1 (and "Send a new code"). */
        async sendCode(again) {
            try {
                const answer = await Api.post("/api/signin", { email: this.typed, ...this.extra() },
                                              { redirectOnSignOut: false });
                if (answer.admin) {
                    this.setStep("admin");
                    this.say("This is an admin account. Enter its password.", { info: true });
                    $("signin-password").focus();
                } else if (answer.verify) {
                    this.setStep("code");
                    this.say(answer.emailed
                        ? `${again ? "A new code is on its way" : "We emailed a code"} to ${this.typed}. `
                            + "Enter it to finish signing in."
                        : "We couldn't email a code to this address. Ask whoever runs this site for "
                            + "your sign-in code, then enter it here.", { info: true });
                    $("signin-code").focus();
                } else {
                    this.done(); // verification is off: already signed in
                }
            } catch (error) {
                if (!again) throw error;
                this.say(error.message);
            }
        }

        async verify() {
            const code = $("signin-code").value.trim();
            if (!code) {
                this.say("Enter the code from your email.");
                return;
            }
            await Api.post("/api/signin/verify", { email: this.typed, code, ...this.extra() },
                           { redirectOnSignOut: false });
            this.done();
        }

        async adminSignIn() {
            const password = $("signin-password").value;
            if (!password) {
                this.say("Enter the admin password.");
                return;
            }
            const passcode = $("signin-passcode").value.trim();
            this.say("Checking… If Duo is on, approve the request on your phone.", { info: true });
            await Api.post("/api/admin/signin", { username: this.typed, password, ...(passcode ? { passcode } : {}) },
                           { redirectOnSignOut: false });
            this.adminDone();
        }
    }

    window.SignInForm = SignInForm;
})();

# Customer-supplied SMTP instead of a platform mail provider

Outbound email from an Instance goes through a mailbox the Customer supplies: SMTP host, port, user, password and sender address are Inputs of the order, declared in the App's Manifest and delivered to the Instance as Fly secrets (never in the Provisioning Payload). This replaces the earlier plan of a platform-wide Resend account with per-Instance credentials on `mail.vitekdvorak.cz`.

Reasons: no third-party vendor and no recurring cost; the sender is the Customer's own domain, which is what their recipients expect and where deliverability is the Customer's existing setup (the first App's current customer already sends through a hosting mailbox); no shared sending domain whose reputation every Customer could damage.

Consequences:
- An App that sends email must declare `sends_email: true` and the five SMTP Inputs in its Manifest; registration rejects a Manifest that sends email without them (checked by vmarket, not left to review).
- vmarket tests the credentials (connect, TLS, login) when the order is placed and again as a Provisioning step, because the Operator's set-password link travels through this mailbox: wrong credentials would leave nobody able to sign in. A failed check fails Provisioning with the server's message.
- The password is kept in vmarket's database only until Provisioning succeeds; afterwards it lives in the Instance's Fly secrets and the stored copy is erased. (The database is replicated by Litestream, so long-lived copies are avoided on purpose.)
- A managed provider (Resend or similar) can be added later as an optional alternative behind the same `Smtp` port without changing Apps.

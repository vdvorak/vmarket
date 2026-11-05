"""Ověření SMTP přihlašovacích údajů zákazníka bez odeslání zprávy.

Port 465 = TLS od začátku, jinak STARTTLS (587 a další). Certifikát serveru se ověřuje stejně jako v aplikaci:
chyba CERTIFICATE_VERIFY_FAILED znamená, že jméno serveru neodpovídá certifikátu (a aplikace by tím mail neodeslala)."""

import smtplib
import ssl

from vmarket.provisioning.ports import AdapterError, PermanentError


class SmtpChecker:
    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def check(self, host: str, port: int, user: str, password: str) -> None:
        context = ssl.create_default_context()
        try:
            if port == 465:
                server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=self.timeout, context=context)
            else:
                server = smtplib.SMTP(host, port, timeout=self.timeout)
            with server:
                server.ehlo()
                if port != 465:
                    server.starttls(context=context)
                    server.ehlo()
                server.login(user, password)
        except smtplib.SMTPAuthenticationError as e:
            raise PermanentError(f"SMTP {host}:{port}: přihlášení odmítnuto ({e.smtp_code}): zkontrolujte uživatele a heslo") from e
        except ssl.SSLCertVerificationError as e:
            raise PermanentError(
                f"SMTP {host}:{port}: certifikát serveru neplatí pro toto jméno ({getattr(e, 'verify_message', e)})"
            ) from e
        except (smtplib.SMTPNotSupportedError, smtplib.SMTPConnectError, ssl.SSLError) as e:
            raise PermanentError(f"SMTP {host}:{port}: server nepodporuje požadované spojení: {e}") from e
        except (TimeoutError, OSError, smtplib.SMTPException) as e:
            raise AdapterError(f"SMTP {host}:{port}: spojení selhalo: {e}") from e

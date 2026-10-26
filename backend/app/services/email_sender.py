"""SMTP email delivery service for Soultie."""

import smtplib
import ssl

from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app


class EmailSenderError(Exception):
    """Base email sender error."""


class EmailConfigurationError(
    EmailSenderError
):
    """Raised when SMTP is not configured."""


class EmailDeliveryError(
    EmailSenderError
):
    """Raised when email delivery fails."""


def _smtp_config():
    host = str(
        current_app.config.get(
            "SMTP_HOST",
            "",
        )
        or ""
    ).strip()

    port = int(
        current_app.config.get(
            "SMTP_PORT",
            587,
        )
    )

    username = str(
        current_app.config.get(
            "SMTP_USERNAME",
            "",
        )
        or ""
    ).strip()

    password = str(
        current_app.config.get(
            "SMTP_PASSWORD",
            "",
        )
        or ""
    )

    from_email = str(
        current_app.config.get(
            "SMTP_FROM_EMAIL",
            "",
        )
        or ""
    ).strip()

    from_name = str(
        current_app.config.get(
            "SMTP_FROM_NAME",
            "Xuoroni",
        )
        or "Xuoroni"
    ).strip()

    use_tls = bool(
        current_app.config.get(
            "SMTP_USE_TLS",
            True,
        )
    )

    timeout = int(
        current_app.config.get(
            "SMTP_TIMEOUT_SECONDS",
            15,
        )
    )

    if not host:
        raise EmailConfigurationError(
            "SMTP_HOST is not configured."
        )

    if not from_email:
        raise EmailConfigurationError(
            (
                "SMTP_FROM_EMAIL is "
                "not configured."
            )
        )

    if port <= 0:
        raise EmailConfigurationError(
            "SMTP_PORT is invalid."
        )

    if timeout <= 0:
        raise EmailConfigurationError(
            (
                "SMTP_TIMEOUT_SECONDS "
                "must be greater than zero."
            )
        )

    if username and not password:
        raise EmailConfigurationError(
            (
                "SMTP_PASSWORD is required "
                "when SMTP_USERNAME is set."
            )
        )

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "from_email": from_email,
        "from_name": from_name,
        "use_tls": use_tls,
        "timeout": timeout,
    }


def send_email(
    *,
    recipient: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
):
    recipient = str(
        recipient or ""
    ).strip()

    subject = str(
        subject or ""
    ).strip()

    text_body = str(
        text_body or ""
    )

    if not recipient:
        raise ValueError(
            "Email recipient is required."
        )

    if not subject:
        raise ValueError(
            "Email subject is required."
        )

    config = _smtp_config()

    message = EmailMessage()

    message["From"] = formataddr(
        (
            config["from_name"],
            config["from_email"],
        )
    )

    message["To"] = recipient
    message["Subject"] = subject

    message.set_content(
        text_body
    )

    if html_body:
        message.add_alternative(
            str(
                html_body
            ),
            subtype="html",
        )

    try:
        with smtplib.SMTP(
            config["host"],
            config["port"],
            timeout=config[
                "timeout"
            ],
        ) as smtp:
            smtp.ehlo()

            if config["use_tls"]:
                context = (
                    ssl.create_default_context()
                )

                smtp.starttls(
                    context=context
                )

                smtp.ehlo()

            if config[
                "username"
            ]:
                smtp.login(
                    config[
                        "username"
                    ],
                    config[
                        "password"
                    ],
                )

            smtp.send_message(
                message
            )

    except (
        smtplib.SMTPException,
        OSError,
    ) as exc:
        raise EmailDeliveryError(
            (
                "Email could not be "
                "delivered."
            )
        ) from exc

    return True


def send_auth_otp_email(
    *,
    recipient: str,
    otp: str,
    expires_in: int,
):
    otp = str(
        otp or ""
    ).strip()

    if not otp:
        raise ValueError(
            "OTP is required."
        )

    expires_in = max(
        int(
            expires_in
        ),
        1,
    )

    minutes = max(
        1,
        (
            expires_in
            + 59
        )
        // 60,
    )

    subject = (
        "Your Xuoroni verification code"
    )

    text_body = (
        "Your Xuoroni verification code is "
        f"{otp}.\n\n"
        "This code expires in "
        f"{minutes} minute"
        f"{'' if minutes == 1 else 's'}.\n\n"
        "If you did not request this code, "
        "you can ignore this email."
    )

    html_body = (
        "<!doctype html>"
        "<html>"
        "<body>"
        "<p>Your Xuoroni verification "
        "code is:</p>"
        f"<p><strong>{otp}</strong></p>"
        "<p>This code expires in "
        f"{minutes} minute"
        f"{'' if minutes == 1 else 's'}."
        "</p>"
        "<p>If you did not request this "
        "code, you can ignore this email."
        "</p>"
        "</body>"
        "</html>"
    )

    return send_email(
        recipient=recipient,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )

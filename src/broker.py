"""Broker connection, in one place.

Connection settings come from the environment with local-Docker defaults, so the
same code runs against the demo broker and against Solace Cloud without edits.

PROPERTY NAMES COME FROM THE LIBRARY, NOT FROM MEMORY.
The first version of this file hand-typed
"solace.messaging.authentication.scheme.basic.user-name", which is wrong twice
over: there is no `scheme.` segment, and it is `username` rather than
`user-name`. The build failed with "Mandatory broker properties are missing",
which is a confusing way to say "your key was ignored because nothing recognised
it" — a silently-dropped setting rather than a rejected one.

Importing the constants makes that class of error impossible: a typo is now an
AttributeError at import time instead of a runtime failure that reads like a
missing credential.
"""
import logging
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import profile as _profile

# Load the selected profile BEFORE the module-level reads below. Anything that
# imports broker gets the right broker without having to remember to do this.
_profile.load(quiet=True)

from solace.messaging.messaging_service import MessagingService
from solace.messaging.config.retry_strategy import RetryStrategy
from solace.messaging.config.transport_security_strategy import TLS
from solace.messaging.config.solace_properties import (
    authentication_properties as auth,
    service_properties as svc,
    transport_layer_properties as transport,
)

HOST = os.environ.get("SOLACE_HOST", "tcp://localhost:55555")
VPN = os.environ.get("SOLACE_VPN", "default")
USER = os.environ.get("SOLACE_USER", "default")
PASSWORD = os.environ.get("SOLACE_PASSWORD", "default")


# --- library log noise ------------------------------------------------------
# The client logs transient transport events at WARNING, including
# "SSL 'SSL-client' cannot read" on a reconnect. Nothing is lost when that
# happens, so these are suppressed to ERROR; DEMO_VERBOSE_SOLACE=1 restores
# them when diagnosing a connection. A genuine connect failure still raises.
if not os.environ.get("DEMO_VERBOSE_SOLACE"):
    for _name in ("solace", "solace.messaging.core", "solace.messaging.connections"):
        logging.getLogger(_name).setLevel(logging.ERROR)


def connect(name: str) -> MessagingService:
    """Connect and return a started MessagingService.

    `name` shows up as the application id on the broker, which makes the
    connections legible in the admin console during a live demo — worth the one
    argument when someone asks "which of these is the credit desk?".
    """
    builder = (
        MessagingService.builder()
        .from_properties({
            transport.HOST: HOST,
            svc.VPN_NAME: VPN,
            auth.SCHEME_BASIC_USER_NAME: USER,
            auth.SCHEME_BASIC_PASSWORD: PASSWORD,
        })
        # Reconnection is the point of half this demo: a consumer that drops and
        # comes back must catch up rather than lose its window. Retry generously.
        .with_reconnection_retry_strategy(
            # 20 retries, 3 SECONDS apart. The interval is MILLISECONDS: (20, 3)
            # spreads twenty attempts over sixty milliseconds, so a transient
            # blip exhausts every retry before the broker can answer and the
            # in-flight acknowledgements are lost to the reconnect.
            RetryStrategy.parametrized_retry(20, 3000))
    )

    # TLS, when the host asks for it.
    #
    # Solace Cloud states plainly on the service creation form that "all unsecure
    # ports are disabled by default", so a cloud broker is tcps:// on 55443 and
    # will simply refuse a plain tcp:// connection. The local Docker broker is
    # tcp:// on 55555. Rather than keep two versions of this file, switch on the
    # scheme - which means the SAME code runs against both, and moving between
    # them is an .env change rather than an edit.
    #
    # Certificate validation is left ON. Solace Cloud presents a certificate from
    # a public CA, so it validates against the system trust store with no extra
    # configuration. Disabling validation would be one line
    # (TLS.create().without_certificate_validation()) and it is deliberately not
    # done: a demo that turns off certificate checking is a bad thing to have on
    # screen in front of enterprise architects, and it would invite a question
    # with no good answer.
    if HOST.lower().startswith("tcps://") or HOST.lower().startswith("wss://"):
        # The trust store is required, and has three traps worth knowing:
        #
        # 1. The underlying C client does not read the OS certificate store, so
        #    with no trust store the connect fails "Failed to load trust store".
        # 2. `trust_store_file_path` wants a DIRECTORY despite the name. A .pem
        #    file fails as "Untrusted certificate", which points at the server.
        # 3. The directory must contain ONLY certificates. certifi's own package
        #    directory handshakes successfully and a short-lived publisher works,
        #    but long-lived receivers then die repeating
        #    "SSL 'SSL-client' cannot read".
        #
        # certifi ships the Mozilla CA bundle and comes in with requests, so no
        # PEM download is needed. Certificate validation stays on.
        import shutil
        import certifi

        cert_dir = Path(__file__).resolve().parent.parent / "certs"
        cert_dir.mkdir(exist_ok=True)
        bundle = cert_dir / "ca-bundle.pem"
        if not bundle.exists():
            shutil.copy(certifi.where(), bundle)

        builder = builder.with_transport_security_strategy(
            TLS.create().with_certificate_validation(
                ignore_expiration=False,
                validate_server_name=True,
                trust_store_file_path=str(cert_dir),
            )
        )

    # Client names are unique per message VPN, so a bare "meridian-{role}"
    # collides in two ordinary cases: a force-killed consumer leaves a ghost
    # connection holding the name until keepalive expires, and starting the
    # window set twice claims it again. The two connections then displace each
    # other, and the churn loses in-flight acknowledgements. The role stays at
    # the front so the console still identifies each desk.
    suffix = uuid.uuid4().hex[:4]
    service = builder.build(f"meridian-{name}-{suffix}")
    service.connect()
    return service


@contextmanager
def session(name: str):
    """Connect, yield the service, and always disconnect."""
    service = connect(name)
    try:
        yield service
    finally:
        service.disconnect()

"""Who is asking, when this repo talks to SEC EDGAR.

SEC's fair-access policy requires every automated request to declare a real
contact in its User-Agent. Requests without one are throttled or refused, and
the correct response to a missing contact is to stop -- not to substitute a
placeholder, which would put a fabricated contact on a request to a federal
system.

The address is an environment variable rather than a constant because this
repository is public and a committed address is permanent:

    SEC_CONTACT_EMAIL   required; no default, by design

Same shape as corpus_paths.py, and for the same reason: committed source may
not carry a value that is specific to whoever happens to be running it. That
module guards machine-local paths, this one guards a personal address, and
tests/test_sec_contact.py keeps both honest.

Read at call time, not at import: a module constant would freeze whatever the
environment held when the first importer ran, and would make importing a fetch
script -- which tests do, for its pure functions -- fail without the variable.
"""

import os
import re

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PRODUCT = "RAG-pipeline-prototype"

VARIABLE = "SEC_CONTACT_EMAIL"

# Deliberately permissive: this rejects "yes", "true", and an empty string --
# values that would pass a truthiness check and reach EDGAR as a contact that
# is not one. It is not an address validator, and does not try to be.
_LOOKS_LIKE_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

_GUIDANCE = (
    f"{VARIABLE} is not set. SEC requires a real contact address in the "
    f"User-Agent of automated requests; set it before fetching, e.g.\n"
    f"  PowerShell:  $env:{VARIABLE} = 'you@example.org'\n"
    f"  bash:        export {VARIABLE}=you@example.org\n"
    f"See SETUP.md. There is deliberately no default -- a fabricated contact "
    f"is worse than no request."
)


def contact() -> str:
    """The configured address. Raises rather than returning a placeholder."""
    value = os.environ.get(VARIABLE, "").strip()
    if not value:
        raise RuntimeError(_GUIDANCE)
    if not _LOOKS_LIKE_ADDRESS.match(value):
        raise RuntimeError(
            f"{VARIABLE} does not look like an email address: {value!r}. "
            f"SEC's policy asks for a contact they can actually reach."
        )
    return value


def user_agent() -> str:
    """The User-Agent header value: product plus contact."""
    return f"{PRODUCT} {contact()}"


class _ContactSession(requests.Session):
    """A session that names its operator on every request, or refuses to send.

    The check sits on the request path rather than in each script's main().
    A resolver called once at startup is a convention, and a convention is one
    refactor away from an anonymous request to a federal system -- so the
    guarantee is structural instead.
    """

    def request(self, method, url, **kwargs):
        self.headers["User-Agent"] = user_agent()
        return super().request(method, url, **kwargs)


def session(pool: int = 4) -> requests.Session:
    """A session configured for EDGAR: contact header, retries, keep-alive.

    The retry policy is not decoration. SEC resets the connection on a run of
    sequential requests that opens a fresh handshake each time -- it reads as
    abuse regardless of how politely the run is paced -- so every caller shares
    one session with backoff on the throttling and transient-server codes.

    `pool` is the only thing callers varied (2 for the calibration fetch, 4
    elsewhere); everything else was identical in all three copies of this
    setup, which is why they are now one.
    """
    built = _ContactSession()
    built.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(total=4, backoff_factor=1.5,
                              status_forcelist=(429, 500, 502, 503, 504),
                              allowed_methods=("GET",)),
            pool_connections=pool,
            pool_maxsize=pool,
        ),
    )
    return built

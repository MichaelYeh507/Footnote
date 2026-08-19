"""The SEC contact address comes from the environment, and no committed file
carries one.

Two things are being protected, and they are different.

**SEC's fair-access policy requires a real contact in the User-Agent.** A
request without one is throttled or blocked, and a request with a fabricated
one is worse than useless -- it is a false contact on a request to a federal
system. So the resolver raises rather than substituting a placeholder: a run
that cannot name its operator must not reach EDGAR at all.

**A personal email in committed source is published permanently.** This repo
is public; four fetch scripts hardcoded the owner's address, and a push would
have put it in git history and in front of every scraper that reads GitHub.
The guard below is the regression check, and it is the same shape as
test_no_machine_local_paths.py: the fix is only worth as much as the thing
that keeps it fixed.

Written before backend/sec_contact.py existed (red first).
"""

import io
import pathlib
import re

import pytest
import requests

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent

import sec_contact  # noqa: E402

VARIABLE = "SEC_CONTACT_EMAIL"


@pytest.fixture(autouse=True)
def _no_ambient_contact(monkeypatch):
    """Never let the developer's own environment decide a test's outcome."""
    monkeypatch.delenv(VARIABLE, raising=False)


# ------------------------------------------------------------- resolution

class TestUserAgent:
    def test_raises_when_the_contact_is_unset(self):
        with pytest.raises(RuntimeError) as exc:
            sec_contact.user_agent()
        assert VARIABLE in str(exc.value)

    def test_raises_when_the_contact_is_blank(self, monkeypatch):
        """`set SEC_CONTACT_EMAIL=` leaves an empty string, not an unset
        variable -- the same shape corpus_paths.py guards against."""
        monkeypatch.setenv(VARIABLE, "   ")
        with pytest.raises(RuntimeError):
            sec_contact.user_agent()

    def test_raises_when_the_contact_is_not_an_address(self, monkeypatch):
        """A UA of 'RAG-pipeline-prototype true' would sail past a truthiness
        check and reach EDGAR as a fabricated contact."""
        monkeypatch.setenv(VARIABLE, "yes")
        with pytest.raises(RuntimeError):
            sec_contact.user_agent()

    def test_carries_product_and_contact(self, monkeypatch):
        monkeypatch.setenv(VARIABLE, "someone@example.org")
        assert sec_contact.user_agent() == (
            f"{sec_contact.PRODUCT} someone@example.org")

    def test_surrounding_whitespace_is_stripped(self, monkeypatch):
        monkeypatch.setenv(VARIABLE, "  someone@example.org \n")
        assert sec_contact.user_agent().endswith("someone@example.org")

    def test_resolved_at_call_time_not_import_time(self, monkeypatch):
        """A module constant would freeze whatever the environment held when
        the first importer ran."""
        monkeypatch.setenv(VARIABLE, "first@example.org")
        first = sec_contact.user_agent()
        monkeypatch.setenv(VARIABLE, "second@example.org")
        assert sec_contact.user_agent() != first


# ----------------------------------------------------------------- session

class _CaptureAdapter(requests.adapters.HTTPAdapter):
    """Answers requests locally, so these tests never touch the network."""

    def __init__(self):
        super().__init__()
        self.seen = None

    def send(self, request, **kwargs):
        self.seen = request
        response = requests.models.Response()
        response.status_code = 200
        response.raw = io.BytesIO(b"ok")
        response.request = request
        return response


class TestSession:
    def test_session_is_constructible_without_a_contact(self):
        """Importing a fetch script must not require the variable -- tests
        import them for their pure functions (test_statement_detector reads
        statement_flags out of fetch_filings)."""
        assert sec_contact.session() is not None

    def test_a_request_without_a_contact_raises_before_sending(self):
        """The check has to sit on the request path. A resolver called only in
        main() is a convention, and a convention is one refactor from sending
        an anonymous request to a federal system."""
        session = sec_contact.session()
        adapter = _CaptureAdapter()
        session.mount("https://", adapter)
        with pytest.raises(RuntimeError):
            session.get("https://data.sec.gov/submissions/CIK0000320193.json")
        assert adapter.seen is None, "the request was sent despite no contact"

    def test_a_request_carries_the_user_agent(self, monkeypatch):
        monkeypatch.setenv(VARIABLE, "someone@example.org")
        session = sec_contact.session()
        adapter = _CaptureAdapter()
        session.mount("https://", adapter)
        session.get("https://data.sec.gov/submissions/CIK0000320193.json")
        assert adapter.seen.headers["User-Agent"] == (
            f"{sec_contact.PRODUCT} someone@example.org")

    def test_the_retry_policy_survives_consolidation(self):
        """The three scripts each mounted an identical Retry; SEC resets the
        connection on sequential requests without one."""
        adapter = sec_contact.session().get_adapter("https://data.sec.gov/")
        retry = adapter.max_retries
        assert retry.total == 4
        assert retry.backoff_factor == 1.5
        assert 429 in retry.status_forcelist and 503 in retry.status_forcelist


# ------------------------------------------------------------------- guard

# This file necessarily contains the shapes it forbids.
SELF = pathlib.Path(__file__).name

SKIP_DIRS = {"venv", "__pycache__", ".pytest_cache", "node_modules"}

# Gitignored internal docs (.gitignore "Internal planning docs" block). They
# are not published, and they legitimately name the owner.
PRIVATE_DOCS = ("HANDOFF-", "HYBRID-RETRIEVAL-SEC-PLAN", "RECON",
                "PROJECT-STANDARDS")

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Documentation placeholders. RFC 2606 reserves example.* for exactly this.
ALLOWED = re.compile(r"@example\.(com|org|net)$", re.I)


def published_files():
    for path in sorted(BACKEND.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == SELF:
            continue
        yield path
    for path in sorted(REPO.glob("*.md")):
        if not any(path.name.startswith(p) for p in PRIVATE_DOCS):
            yield path


def test_published_files_were_found():
    """A glob matching nothing would make the guard below pass vacuously."""
    files = list(published_files())
    assert len(files) > 20, f"only found {len(files)} files"
    assert any(f.suffix == ".md" for f in files), "no docs scanned"


@pytest.mark.parametrize(
    "path", list(published_files()), ids=lambda p: p.name
)
def test_no_published_file_carries_an_email_address(path):
    offenders = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(),
                                  start=1):
        for match in EMAIL.finditer(line):
            if not ALLOWED.search(match.group(0)):
                offenders.append(f"  line {lineno}: {match.group(0)}")
    assert not offenders, (
        f"{path.name} hardcodes an email address -- this repo is public, and a "
        f"committed address is permanent:\n" + "\n".join(offenders)
    )


SEC_SCRIPTS = ("fetch_filings.py", "fetch_calibration_filings.py",
               "fetch_sp500_snapshot.py", "select_issuers.py")


@pytest.mark.parametrize("name", SEC_SCRIPTS)
def test_sec_scripts_route_through_sec_contact(name):
    """Structural, not stylistic: a script building its own bare Session is a
    script that can send an anonymous request."""
    text = (BACKEND / "scripts" / name).read_text(encoding="utf-8")
    assert "sec_contact" in text, f"{name} does not use sec_contact"
    assert "requests.Session()" not in text, (
        f"{name} builds its own session instead of sec_contact.session()")

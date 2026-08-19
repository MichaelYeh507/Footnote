"""The DDL connection: required, never defaulted, never guessed.

`services/supabase_client.py` talks PostgREST, which cannot issue DDL and
cannot COPY. Phase 3 needs both, so there is a second connection path, and its
credential gets the same treatment as `SEC_CONTACT_EMAIL`: required, no
default, raises rather than substituting something plausible.

The failure this prevents is narrow but expensive. A default of
`postgresql://localhost/postgres` would connect on a developer machine running
any local Postgres, build both indexes over an empty table, and report success.
Every recall number computed afterwards would be zero for a reason that has
nothing to do with retrieval.

Written before backend/database.py existed (red first).
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import database  # noqa: E402


@pytest.fixture
def no_env_file(tmp_path):
    """A path that does not exist, so only os.environ is consulted."""
    return tmp_path / "absent.env"


@pytest.fixture(autouse=True)
def clean_environ(monkeypatch):
    monkeypatch.delenv(database.VARIABLE, raising=False)


class TestTheVariableIsRequired:

    def test_raises_when_unset(self, no_env_file):
        with pytest.raises(RuntimeError) as raised:
            database.url(env_file=no_env_file)
        assert database.VARIABLE in str(raised.value)

    def test_the_message_says_where_to_get_the_value(self, no_env_file):
        """A refusal that does not say how to fix it gets worked around."""
        with pytest.raises(RuntimeError) as raised:
            database.url(env_file=no_env_file)
        message = str(raised.value)
        assert "Supabase" in message
        assert ".env" in message

    def test_raises_on_whitespace_only(self, monkeypatch, no_env_file):
        """`DATABASE_URL=` leaves an empty string, not an absent variable.

        The same shape as the `.strip()` in corpus_paths, and for the same
        reason: the failure would otherwise be a wrong answer rather than an
        error.

        Note, because perturbation showed it: deleting the `.strip()` does NOT
        make this test fail — the scheme check catches a whitespace-only value
        on the way past. What actually pins the strip is the test below.
        """
        monkeypatch.setenv(database.VARIABLE, "   ")
        with pytest.raises(RuntimeError):
            database.url(env_file=no_env_file)

    def test_a_pasted_dsn_keeps_working_with_whitespace_around_it(
            self, monkeypatch, no_env_file):
        """This is the test the `.strip()` exists for.

        A DSN copied out of the Supabase dashboard arrives with a trailing
        newline more often than not. Without the strip the scheme check still
        passes -- leading whitespace is the failing direction -- and psycopg
        then fails on a DSN that is correct apart from a space.
        """
        monkeypatch.setenv(database.VARIABLE, "  postgresql://u:p@h:5432/db\n")
        assert database.url(env_file=no_env_file) == "postgresql://u:p@h:5432/db"

    def test_raises_on_a_value_that_is_not_a_postgres_url(self, monkeypatch,
                                                          no_env_file):
        """Rejects values that would pass a truthiness check.

        The binding case is pasting the Supabase project URL — an https:// API
        endpoint — into DATABASE_URL. psycopg's own error for that is about DSN
        parsing and does not mention which of the project's several URLs is
        wanted.
        """
        monkeypatch.setenv(database.VARIABLE, "https://abc.supabase.co")
        with pytest.raises(RuntimeError) as raised:
            database.url(env_file=no_env_file)
        assert "postgres" in str(raised.value).lower()


class TestWhereTheValueComesFrom:

    def test_environment_is_used_when_set(self, monkeypatch, no_env_file):
        monkeypatch.setenv(database.VARIABLE, "postgresql://u:p@h:5432/db")
        assert database.url(env_file=no_env_file) == "postgresql://u:p@h:5432/db"

    def test_env_file_is_the_fallback(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(f"{database.VARIABLE}=postgresql://u:p@h:5432/db\n",
                       encoding="utf-8")
        assert database.url(env_file=env) == "postgresql://u:p@h:5432/db"

    def test_environment_wins_over_the_env_file(self, monkeypatch, tmp_path):
        """One override, one direction. A script run with an explicit variable
        must not silently use a different database than the one named."""
        env = tmp_path / ".env"
        env.write_text(f"{database.VARIABLE}=postgresql://from:file@h/db\n",
                       encoding="utf-8")
        monkeypatch.setenv(database.VARIABLE, "postgresql://from:env@h/db")
        assert database.url(env_file=env) == "postgresql://from:env@h/db"

    def test_postgres_scheme_is_accepted_too(self, monkeypatch, no_env_file):
        """Supabase's dashboard hands out `postgresql://`; libpq also accepts
        `postgres://`, and both appear in real connection strings."""
        monkeypatch.setenv(database.VARIABLE, "postgres://u:p@h:5432/db")
        assert database.url(env_file=no_env_file).startswith("postgres://")


class TestTheUnencodedPasswordTrap:
    """The defect that cost a session's worth of misdiagnosis.

    Supabase generates database passwords containing `@`. Pasted raw into a
    URI, libpq splits the userinfo at the FIRST `@`, so half the password
    becomes part of the hostname -- and the error it reports is
    `getaddrinfo failed`, which reads as a DNS or network problem and sends you
    looking at the wrong thing entirely. The same DSN works fine when passed as
    keyword arguments, which makes it look like a driver bug.

    The fix is percent-encoding (`%40`). The point of this guard is that the
    refusal says so, instead of the resolver saying something unrelated.
    """

    def test_an_unencoded_at_in_the_password_is_refused(self, monkeypatch,
                                                        no_env_file):
        monkeypatch.setenv(database.VARIABLE,
                           "postgresql://user:p@ss@host:5432/db")
        with pytest.raises(RuntimeError) as raised:
            database.url(env_file=no_env_file)
        assert "%40" in str(raised.value)

    def test_the_refusal_does_not_echo_the_password(self, monkeypatch,
                                                    no_env_file):
        """A refusal that prints the credential it is complaining about puts it
        in the scrollback, which is the thing `redacted` exists to prevent."""
        monkeypatch.setenv(database.VARIABLE,
                           "postgresql://user:hunter2@ss@host:5432/db")
        with pytest.raises(RuntimeError) as raised:
            database.url(env_file=no_env_file)
        assert "hunter2" not in str(raised.value)

    def test_a_properly_encoded_password_is_accepted(self, monkeypatch,
                                                     no_env_file):
        monkeypatch.setenv(database.VARIABLE,
                           "postgresql://user:p%40ss@host:5432/db")
        assert database.url(env_file=no_env_file) == \
            "postgresql://user:p%40ss@host:5432/db"

    def test_an_ordinary_password_is_unaffected(self, monkeypatch, no_env_file):
        monkeypatch.setenv(database.VARIABLE,
                           "postgresql://user:plainpassword@host:5432/db")
        assert database.url(env_file=no_env_file).endswith("/db")

    def test_a_bracket_in_the_userinfo_is_refused(self, monkeypatch,
                                                  no_env_file):
        """`[` makes both libpq and Python's urlsplit read the netloc as a
        bracketed IPv6 host, which fails in a different but equally confusing
        way."""
        monkeypatch.setenv(database.VARIABLE,
                           "postgresql://user:p[ss@host:5432/db")
        with pytest.raises(RuntimeError):
            database.url(env_file=no_env_file)


class TestTheCredentialIsNotLeaked:

    def test_redacts_the_password(self):
        """Refusals and progress lines print the DSN. A password in a terminal
        scrollback, a CI log or a pasted traceback is a rotated credential."""
        redacted = database.redacted("postgresql://user:hunter2@host:5432/db")
        assert "hunter2" not in redacted
        assert "host:5432" in redacted

    def test_redaction_survives_a_dsn_with_no_password(self):
        assert "host" in database.redacted("postgresql://host:5432/db")

    def test_redaction_handles_a_password_containing_an_at_sign(self):
        """Supabase generates passwords with punctuation; splitting on the
        first `@` instead of the last puts part of the password in the host.

        Asserted as an exact string, not as `"p@ss" not in ...`. Perturbation
        showed why: a first-`@` split yields
        `postgresql://user:***@ss@host:5432/db`, which leaks `ss` into the host
        while containing neither the substring `p@ss` nor anything else the
        weaker assertion looked for. It passed while leaking.
        """
        assert (database.redacted("postgresql://user:p@ss@host:5432/db")
                == "postgresql://user:***@host:5432/db")

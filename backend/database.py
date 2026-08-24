"""The direct Postgres connection, for the things PostgREST cannot do.

`services/supabase_client.py` is the application's database client and it talks
PostgREST. PostgREST cannot issue DDL and cannot COPY, and Phase 3 needs both:
migration 003 creates a table, a GIN index and an HNSW index, and the loader
writes 11,621 rows. So there is a second path to the same database, used by
migrations, by the corpus-loading scripts, and (since the Phase 4/5 QA surface,
2026-08-23) by `POST /api/qa` -- the retrieval arms are SQL over
`tsvector`/`pgvector`, which PostgREST cannot express. Everything else in the
API stays on PostgREST.

    DATABASE_URL   required; no default, by design

No default, for the reason `sec_contact.py` gives about its own variable. A
plausible default here is worse than a missing one: `postgresql://localhost/postgres`
would connect on any developer machine running a local Postgres, create the
table, build both indexes over zero rows and report success. Every recall
number computed afterwards would be zero, and nothing in the output would say
why. A refusal costs one error message; a wrong default costs a measurement.

Read at call time rather than at import, so importing a script for its pure
functions -- which the tests do -- does not require the variable to be set.

The value is the *session pooler / direct connection* string from Supabase's
dashboard (Project Settings > Database > Connection string), not the project
API URL that `SUPABASE_URL` holds. Both are URLs and only one of them is a DSN,
which is why `url()` checks the scheme rather than only the emptiness.
"""

import os
import pathlib

import psycopg
from dotenv import dotenv_values

BACKEND = pathlib.Path(__file__).resolve().parent

VARIABLE = "DATABASE_URL"

# libpq accepts both spellings and both appear in real connection strings.
_SCHEMES = ("postgresql://", "postgres://")

_GUIDANCE = (
    f"{VARIABLE} is not set. Migrations and the chunk loader need a direct "
    f"Postgres connection; PostgREST, which services/supabase_client.py uses, "
    f"cannot issue DDL.\n"
    f"Get it from the Supabase dashboard: Project Settings > Database > "
    f"Connection string (URI). It is NOT the project API URL in SUPABASE_URL.\n"
    f"Put it in backend/.env as\n"
    # Angle-bracket placeholders, not dotted ones: the dotted form reads as an
    # email address to tests/test_sec_contact.py, which scans every published
    # file because this repo is public and a committed address is permanent.
    f"  {VARIABLE}=postgresql://postgres:<password>@<host>:5432/postgres\n"
    f"or set it in the environment for one command:\n"
    f"  PowerShell:  $env:{VARIABLE} = '...'\n"
    f"  bash:        export {VARIABLE}=...\n"
    f"There is deliberately no default -- a default would connect to some "
    f"other database and build the indexes over an empty table."
)


def url(env_file: pathlib.Path | None = None) -> str:
    """The configured DSN. Raises rather than returning a plausible default.

    The environment wins over `backend/.env` so that a script run with an
    explicit variable cannot silently use a different database than the one
    named on the command line. `env_file` is a parameter rather than a constant
    so the tests can point it at a path that does not exist -- otherwise a
    developer machine with a real `.env` would pass the "raises when unset"
    tests for the wrong reason.
    """
    value = os.environ.get(VARIABLE, "").strip()
    if not value:
        path = BACKEND / ".env" if env_file is None else env_file
        if path.exists():
            value = (dotenv_values(path).get(VARIABLE) or "").strip()
    if not value:
        raise RuntimeError(_GUIDANCE)
    if not value.startswith(_SCHEMES):
        raise RuntimeError(
            f"{VARIABLE} is not a Postgres connection string: "
            f"{redacted(value)!r}. It must begin with one of {_SCHEMES}. "
            f"The most common mistake is pasting the Supabase project API URL, "
            f"which belongs in SUPABASE_URL."
        )
    _refuse_unencoded_userinfo(value)
    return value


# Characters that must be percent-encoded inside a URI's userinfo. `@` is the
# one that actually bites: Supabase generates passwords containing it.
_MUST_ENCODE = {"@": "%40", "[": "%5B", "]": "%5D", "/": "%2F",
                "?": "%3F", "#": "%23"}


def _refuse_unencoded_userinfo(dsn: str) -> None:
    """Catch a raw password in a URI before libpq mangles it.

    libpq splits the userinfo at the FIRST `@`, so a password containing one
    puts half of itself into the hostname. What comes back is
    `getaddrinfo failed` -- an error about name resolution, for a problem that
    is nothing of the kind. The same credentials passed as keyword arguments
    connect fine, which makes it look like a driver bug rather than an encoding
    one. This refusal exists because that misdiagnosis is expensive and the
    fix is one substitution.

    Nothing here echoes the password: the message names the character and its
    replacement, never the value.
    """
    for scheme in _SCHEMES:
        if not dsn.startswith(scheme):
            continue
        rest = dsn[len(scheme):]
        userinfo, separator, _host = rest.rpartition("@")
        if not separator:
            return
        offenders = sorted({c for c in userinfo if c in _MUST_ENCODE})
        if offenders:
            fixes = ", ".join(f"{c!r} -> {_MUST_ENCODE[c]}" for c in offenders)
            raise RuntimeError(
                f"{VARIABLE} has unencoded characters in its username or "
                f"password: {offenders}. A URI reserves them, so libpq parses "
                f"the connection string wrongly -- typically reporting "
                f"'getaddrinfo failed', which looks like a DNS problem and is "
                f"not one. Percent-encode them ({fixes}) and try again. "
                f"Supabase-generated passwords commonly contain '@'."
            )
        return


def redacted(dsn: str) -> str:
    """The DSN with its password removed, for printing.

    Progress lines and refusals name the database they are talking to, and a
    password in a terminal scrollback, a CI log or a pasted traceback is a
    credential that has to be rotated. Splits on the LAST `@` of the userinfo
    section: Supabase-generated passwords contain punctuation, and splitting on
    the first `@` puts part of the password into the host.
    """
    for scheme in _SCHEMES:
        if dsn.startswith(scheme):
            rest = dsn[len(scheme):]
            if "@" not in rest:
                return dsn
            userinfo, _, host = rest.rpartition("@")
            user = userinfo.split(":", 1)[0]
            return f"{scheme}{user}:***@{host}"
    return dsn


def connect(env_file: pathlib.Path | None = None, autocommit: bool = False):
    """A psycopg connection to the configured database.

    `autocommit=False` by default: the loader wraps its whole write in one
    transaction so a failed guard leaves no partial store behind. DDL is the
    exception -- `create index ... hnsw` is fine inside a transaction, but
    `create extension` on a fresh project is easier to reason about outside one.
    """
    return psycopg.connect(url(env_file=env_file), autocommit=autocommit)

import os

# Must run before any app module is imported. services/openai_structurer.py and
# services/supabase_client.py both construct their clients at module scope, so an
# unset key raises at import time rather than at call time.
#
# These also shield the suite from a real .env: load_dotenv() does not override
# variables that are already set, so seeding dummies here keeps tests off live
# services even on a machine with working credentials.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-a-real-key")
os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")

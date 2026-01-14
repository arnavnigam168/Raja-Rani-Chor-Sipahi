import os
from supabase import create_client

# --- robust env loading (Streamlit + Windows safe) ---

SUPABASE_URL = None
SUPABASE_ANON_KEY = None

try:
    import streamlit as st
    SUPABASE_URL = st.secrets.get("https://nootrmsytmhyxbcvydmp.supabase.co")
    SUPABASE_ANON_KEY = st.secrets.get("zNjc3MDcsImV4cCI6MjA4Mzk0MzcwN30.-yixSUlICEk")
except Exception:
    pass

# 2. Fallback to .env using ABSOLUTE PATH
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    from dotenv import load_dotenv

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    ENV_PATH = os.path.join(BASE_DIR, ".env")

    load_dotenv(dotenv_path=ENV_PATH, override=True)

    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

# 3. Hard fail if still missing
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError(
        "Supabase credentials not found. "
        "Checked Streamlit secrets and .env at project root."
    )

supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

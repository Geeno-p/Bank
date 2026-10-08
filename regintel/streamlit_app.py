import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

try:  # Streamlit Cloud: copy secrets into env vars before settings are first read
    for k, v in st.secrets.items():
        if isinstance(v, (str, int, float, bool)):
            os.environ.setdefault(k.upper(), str(v))
except Exception:  # noqa: BLE001 (no secrets.toml locally: .env is used instead)
    pass

from regintel.deploy import bootstrap  # noqa: E402
from regintel.ui.app import main  # noqa: E402

st.cache_resource(show_spinner=False)(bootstrap)()
main()
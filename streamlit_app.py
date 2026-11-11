"""Streamlit UI: pick columns, type a request, see the outline, SQL and review verdict.

All logic lives in `app/`; this file only collects input, calls `chain.run()` once and renders.
Modules are imported as `from app import chain, config` so tests can replace `chain.run`
and `config.load_settings`.
"""

import streamlit as st

from app import chain, config, observability
from app.chain import ChainResult, Status
from app.planner import MAX_REQUEST_CHARS
from app.schema import COLUMN_NAMES, COLUMNS, TABLE_NAME

st.set_page_config(page_title="SQL Script Generator", page_icon="🧮")


# --- 12.5 logging and tracing: set up once per process, not on every rerun ---
@st.cache_resource
def _init_observability() -> bool:
    observability.setup_logging()
    return observability.setup_tracing()


_init_observability()

# --- 7.1 header and schema --------------------------------------------------
st.title("SQL Script Generator")
st.caption(
    f"Generates BigQuery SQL for the `{TABLE_NAME}` table. "
    "No data is queried; this app only writes and checks SQL."
)
with st.expander(f"Schema: {TABLE_NAME}", expanded=True):
    st.table(
        [{"column": c.name, "type": c.sql_type, "description": c.description} for c in COLUMNS]
    )

# --- 7.2 configuration check -------------------------------------------------
try:
    config.load_settings()
except config.ConfigError as err:
    st.error(f"App is not configured: {err}")
    st.stop()

# --- 7.3 input form ----------------------------------------------------------
with st.form("generate"):
    columns = st.multiselect(
        "Columns",
        options=COLUMN_NAMES,
        default=[],
        placeholder=f"Choose columns from the {TABLE_NAME} table",
    )
    request = st.text_area(
        "Request",
        max_chars=MAX_REQUEST_CHARS,
        placeholder="e.g. Top 5 products by total sales",
    )
    submitted = st.form_submit_button("Generate SQL", type="primary")

# --- 7.4 run the chain -------------------------------------------------------
if submitted:
    with st.spinner("Planning → writing SQL → reviewing…"):
        st.session_state["result"] = chain.run(request, columns)


# --- 7.5 render the result ---------------------------------------------------
def _show_plan(result: ChainResult) -> None:
    if result.outline:
        st.subheader("1. Plan")
        st.text(result.outline)


def _show_sql(result: ChainResult, label: str = "2. SQL") -> None:
    sql = (result.review.formatted_sql if result.review else None) or result.sql
    if sql:
        st.subheader(label)
        st.code(sql, language="sql")


def render(result: ChainResult) -> None:
    if result.status is Status.OK:
        st.success("✅ SQL passed all checks")
        _show_plan(result)
        _show_sql(result)
        st.download_button(
            "Download .sql",
            data=result.review.formatted_sql or result.sql,
            file_name="query.sql",
            mime="text/plain",
        )
    elif result.status is Status.REVIEW_FAILED:
        st.error("❌ The generated SQL failed the review checks")
        _show_plan(result)
        _show_sql(result, label="2. SQL (not valid, shown for reference)")
        st.subheader("3. Review")
        st.markdown("\n".join(f"- {e}" for e in result.review.errors))
    elif result.status is Status.CANNOT_ANSWER:
        st.warning(f"⚠️ {result.message}. Try selecting more columns.")
    elif result.status is Status.INPUT_ERROR:
        st.warning(f"⚠️ {result.message}")
    else:  # CODER_ERROR, LLM_ERROR, CONFIG_ERROR
        st.error(f"❌ {result.message}")
        _show_plan(result)
    if result.prompt_version:  # Phase 16: which prompt version produced this result
        st.caption(f"Prompt version: {result.prompt_version}")


if "result" in st.session_state:
    render(st.session_state["result"])

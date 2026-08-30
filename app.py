import altair as alt
import pandas as pd
import streamlit as st

from src import config, dashboard
from src.agent import run_turn
from src.leadership_update import generate as generate_leadership_update
from src.monday_client import MondayAPIError

st.set_page_config(page_title="Skylark BI Agent", page_icon="📊", layout="wide")

ASSISTANT_AVATAR = "📊"
USER_AVATAR = "🧑‍💼"

# Validated palette (dataviz skill reference palette) -- single sequential
# hue for magnitude bars, fixed status roles for state-coded ones.
BLUE = "#2a78d6"
INK_SECONDARY = "#52514e"
GRID = "#e1e0d9"
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_CRITICAL = "#d03b3b"

st.markdown(
    """
    <style>
    [data-testid="stChatMessage"] { border-radius: 14px; padding: 0.25rem 0.5rem; }
    .sd-hero { padding: 0.4rem 0 1.1rem 0; border-bottom: 1px solid #E4E9F2; margin-bottom: 1.1rem; }
    .sd-hero h1 { font-size: 1.7rem; margin-bottom: 0.15rem; }
    .sd-hero p { color: #5B6472; font-size: 0.95rem; margin: 0; }
    .sd-badge {
        display: inline-block; font-size: 0.72rem; font-weight: 600; letter-spacing: 0.02em;
        color: #2a78d6; background: #E9EFFD; border-radius: 999px; padding: 0.15rem 0.6rem;
        margin-bottom: 0.5rem;
    }
    div[data-testid="stMetric"] {
        background: #F1F5FB; border-radius: 10px; padding: 0.6rem 0.8rem; border: 1px solid #E4E9F2;
    }
    </style>
    <div class="sd-hero">
        <span class="sd-badge">LIVE · monday.com</span>
        <h1>📊 Skylark Drones — BI Agent</h1>
        <p>Ask founder-level questions about deals and work orders. Every answer is pulled live
        from monday.com and cross-checked against real data-quality gaps — nothing is cached from a CSV.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

missing_config = []
if not config.GOOGLE_API_KEY:
    missing_config.append("GOOGLE_API_KEY")
if not config.MONDAY_API_TOKEN:
    missing_config.append("MONDAY_API_TOKEN")

if missing_config:
    st.error(
        "Missing configuration: " + ", ".join(missing_config) + ". "
        "Set these as environment variables or in `.streamlit/secrets.toml` "
        "(see README.md) before using the agent."
    )
    with st.expander("Debug: what Streamlit secrets are actually visible?"):
        try:
            visible_keys = sorted(st.secrets.keys())
            st.write(f"{len(visible_keys)} secret key(s) found:")
            st.code("\n".join(repr(k) for k in visible_keys) or "(none)")
        except Exception as e:  # noqa: BLE001
            st.write(f"Could not read st.secrets at all: {e}")
    st.stop()


@st.cache_data(ttl=120, show_spinner=False)
def _board_row_count(board_name: str):
    from src.data_service import get_board_data
    data = get_board_data(board_name)
    return data["row_count"], data["resolved_board_id"]


with st.sidebar:
    with st.container(border=True):
        st.markdown("**🔌 Connected boards**")
        for label, board in (
            ("Deals", config.MONDAY_DEALS_BOARD),
            ("Work Orders", config.MONDAY_WORK_ORDERS_BOARD),
        ):
            try:
                count, _ = _board_row_count(board)
                st.metric(label, f"{count} rows", help=f"monday.com board: {board}")
            except MondayAPIError as e:
                st.error(f"{label}: {e}", icon="⚠️")

        if st.button("🔄 Re-check connection", use_container_width=True):
            _board_row_count.clear()
            try:
                from src.data_service import get_client
                boards = get_client().list_boards()
                st.success(f"Connected — {len(boards)} board(s) visible to this token.")
            except MondayAPIError as e:
                st.error(str(e))

    with st.container(border=True):
        st.markdown("**📋 Leadership update**")
        st.caption("Exec-ready markdown brief, generated live.")
        if st.button("Prepare leadership update", type="primary", use_container_width=True):
            with st.spinner("Pulling live data and drafting update..."):
                try:
                    update_text = generate_leadership_update()
                    st.session_state["leadership_update"] = update_text
                except Exception as e:  # noqa: BLE001
                    st.error(f"Failed to generate update: {e}")

        if "leadership_update" in st.session_state:
            st.download_button(
                "⬇ Download update (.md)",
                data=st.session_state["leadership_update"],
                file_name="leadership_update.md",
                mime="text/markdown",
                use_container_width=True,
            )

    if st.button("🗑 Clear conversation", use_container_width=True):
        st.session_state["messages"] = []
        st.session_state.pop("leadership_update", None)
        st.rerun()

    st.caption("Built on Gemini + monday.com's live API · read-only integration")

if "messages" not in st.session_state:
    st.session_state["messages"] = []


def _bar_chart(records: list[dict], cat_field: str, value_prefix: str = "") -> alt.Chart | None:
    if not records:
        return None
    df = pd.DataFrame(records).sort_values("value", ascending=False)
    bars = (
        alt.Chart(df)
        .mark_bar(color=BLUE, cornerRadiusEnd=3, size=18)
        .encode(
            x=alt.X("value:Q", title=None, axis=alt.Axis(grid=True, gridColor=GRID, tickColor=GRID)),
            y=alt.Y(f"{cat_field}:N", title=None, sort="-x"),
            tooltip=[alt.Tooltip(f"{cat_field}:N", title="Category"), alt.Tooltip("value:Q", title="Value", format=",.0f")],
        )
    )
    labels = bars.mark_text(align="left", dx=5, color=INK_SECONDARY, fontSize=11).encode(
        text=alt.Text("value:Q", format=f"{value_prefix},.0f")
    )
    return (bars + labels).properties(height=max(28 * len(df), 60)).configure_view(strokeWidth=0)


def _render_dashboard():
    with st.spinner("Reading live board data..."):
        try:
            data = dashboard.load()
        except Exception as e:  # noqa: BLE001
            st.error(f"Couldn't load dashboard data: {e}")
            return

    if not data:
        st.info(
            "Dashboard tiles need columns named things like 'Deal Status' / 'Sector' -- "
            "none matched on the connected boards, so nothing to show here. The chat "
            "agent doesn't have this limitation; ask it directly instead."
        )
        return

    k1, k2, k3, k4 = st.columns(4)
    if data.get("open_pipeline_value") is not None:
        k1.metric("Open pipeline value", f"₹{data['open_pipeline_value']:,.0f}")
    if data.get("win_rate") is not None:
        k2.metric("Win rate (by count)", f"{data['win_rate']}%", help=f"{data['won_count']} won vs {data['dead_count']} lost")
    if data.get("completion_pct") is not None:
        k3.metric("Work orders completed", f"{data['completion_pct']}%", help=f"of {data['wo_total']} total work orders")
    if data.get("not_started_count") is not None:
        delta_color = "inverse" if data["not_started_count"] > 0 else "off"
        k4.metric("Not started", data["not_started_count"], delta="needs attention" if data["not_started_count"] else None, delta_color=delta_color)

    st.caption("KPIs computed directly via pandas from live monday.com data (not routed through the LLM).")
    st.divider()

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Open pipeline value by sector**")
        pbs = data.get("pipeline_by_sector")
        chart = _bar_chart(pbs["records"], pbs["field"], value_prefix="₹") if pbs else None
        if chart is not None:
            st.altair_chart(chart, use_container_width=True)
        else:
            st.caption("Not available for this board's schema.")
    with c2:
        st.markdown("**Work order status breakdown**")
        wsb = data.get("wo_status_breakdown")
        chart = _bar_chart(wsb["records"], wsb["field"]) if wsb else None
        if chart is not None:
            st.altair_chart(chart, use_container_width=True)
        else:
            st.caption("Not available for this board's schema.")


tab_chat, tab_dashboard = st.tabs(["💬 Ask the agent", "📈 Live dashboard"])

with tab_dashboard:
    _render_dashboard()

with tab_chat:
    if "leadership_update" in st.session_state:
        with st.expander("📋 Latest leadership update", expanded=True):
            st.markdown(st.session_state["leadership_update"])

    EXAMPLE_QUESTIONS = [
        "What's our total open pipeline value by sector?",
        "Any won deals without a matching work order yet?",
        "How complete is our deal data? What caveats should I know?",
    ]

    if not st.session_state["messages"]:
        st.caption("Try one of these, or ask your own question below:")
        cols = st.columns(len(EXAMPLE_QUESTIONS))
        for col, q in zip(cols, EXAMPLE_QUESTIONS):
            if col.button(q, use_container_width=True):
                st.session_state["prefill"] = q

    for msg in st.session_state["messages"]:
        if msg["role"] not in ("user", "assistant") or not msg.get("content"):
            continue
        avatar = ASSISTANT_AVATAR if msg["role"] == "assistant" else USER_AVATAR
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])

    user_input = st.chat_input("e.g. How's our pipeline looking for the energy sector this quarter?")
    if not user_input and st.session_state.get("prefill"):
        user_input = st.session_state.pop("prefill")

    if user_input:
        st.session_state["messages"].append({"role": "user", "content": user_input})
        with st.chat_message("user", avatar=USER_AVATAR):
            st.markdown(user_input)

        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            trace_container = st.status("Querying monday.com...", expanded=False)
            trace_log = []

            def on_tool_call(name, tool_input, result):
                trace_log.append(f"**{name}**  \n`{tool_input}`")
                trace_container.update(label=f"Called {name}...")

            try:
                final_text, updated_conversation = run_turn(
                    st.session_state["messages"], on_tool_call=on_tool_call
                )
                trace_container.update(label="Done", state="complete")
                if trace_log:
                    with trace_container:
                        for line in trace_log:
                            st.markdown(line)
                st.markdown(final_text)
                st.session_state["messages"] = updated_conversation
            except Exception as e:  # noqa: BLE001
                trace_container.update(label="Error", state="error")
                st.error(f"The agent hit an error: {e}")

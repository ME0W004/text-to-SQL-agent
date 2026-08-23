# src/web/app.py

import os
import streamlit as st
import requests
import pandas as pd
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Text-to-SQL Agent",
    page_icon="📊",
    layout="wide"
)

API_URL = os.getenv("API_URL", "http://localhost:8000/api/v1")

if "messages" not in st.session_state:
    st.session_state.messages = []

if "session_id" not in st.session_state:
    st.session_state.session_id = None


def send_question(question: str, session_id: Optional[str] = None) -> Optional[dict]:
    """Send a question to the API and return the response."""
    try:
        # Log the session_id being sent
        print(f"Sending question with session_id: {session_id}")

        response = requests.post(
            f"{API_URL}/ask",
            json={
                "question": question,
                "session_id": session_id
            },
            timeout=60
        )
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"API error: {response.status_code}")
            return None
    except requests.exceptions.ConnectionError:
        st.error("Connection to API failed. Make sure the API server is running.")
        return None
    except Exception as e:
        st.error(f"Error: {e}")
        return None


# ============================================================
# RTL / Right-to-Left Styling Only
# ============================================================

st.markdown(
    """
    <style>

    /* کل صفحه */
    .stApp {
        direction: rtl;
        text-align: right;
    }

    /* محتوای اصلی */
    .main .block-container {
        direction: rtl;
        text-align: right;
    }

    /* سایدبار */
    [data-testid="stSidebar"] {
        direction: rtl;
        text-align: right;
    }

    [data-testid="stSidebar"] * {
        text-align: right;
    }

    /* عنوان‌ها و متن‌ها */
    h1, h2, h3, h4, h5, h6,
    p, label, div {
        text-align: right;
    }

    /* چت */
    [data-testid="stChatMessage"] {
        direction: rtl;
        text-align: right;
    }

    [data-testid="stChatMessageContent"] {
        direction: rtl;
        text-align: right;
    }

    /* ورودی چت */
    [data-testid="stChatInput"] {
        direction: rtl;
    }

    [data-testid="stChatInput"] textarea {
        direction: rtl;
        text-align: right;
    }

    [data-testid="stChatInput"] textarea::placeholder {
        direction: rtl;
        text-align: right;
    }

    /* دکمه‌ها */
    button {
        direction: rtl;
        text-align: center;
    }

    /* Expander */
    [data-testid="stExpander"] {
        direction: rtl;
        text-align: right;
    }

    [data-testid="stExpander"] summary {
        direction: rtl;
        text-align: right;
    }

    /* Alert ها */
    [data-testid="stAlert"] {
        direction: rtl;
        text-align: right;
    }

    /* Caption */
    [data-testid="stCaptionContainer"] {
        direction: rtl;
        text-align: right;
    }

    /* جدول */
    [data-testid="stDataFrame"] {
        direction: rtl;
    }

    /* متن داخل سلول‌های جدول */
    [data-testid="stDataFrame"] * {
        direction: rtl;
    }

    /* کد SQL و code block ها باید چپ به راست بمانند */
    code,
    pre,
    [data-testid="stCode"] {
        direction: ltr !important;
        text-align: left !important;
    }

    /* Markdown code */
    .stMarkdown code {
        direction: ltr !important;
        text-align: left !important;
    }

    /* جلوگیری از RTL شدن آیکون‌ها و المان‌های فنی */
    svg {
        direction: ltr;
    }

    </style>
    """,
    unsafe_allow_html=True
)


st.title("Text-to-SQL Agent")
st.markdown("Ask your question in natural language and get SQL results.")

# Sidebar for session management
with st.sidebar:
    st.markdown("### Session Management")

    if st.button("Start New Session"):
        st.session_state.messages = []
        st.session_state.session_id = None
        st.rerun()

    st.markdown("---")

    st.markdown("### Information")
    st.markdown(f"**Messages:** {len(st.session_state.messages)}")

    if st.session_state.session_id:
        st.markdown(
            f"**Session ID:** {st.session_state.session_id[:8]}..."
        )


# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):

        if message["role"] == "assistant":
            st.markdown(
                message["content"],
                unsafe_allow_html=True
            )

            if "rows" in message and message["rows"]:
                df = pd.DataFrame(message["rows"])
                st.dataframe(
                    df,
                    width='stretch'
                )

            if "sql" in message and message["sql"]:
                with st.expander("View SQL Query"):
                    st.code(
                        message["sql"],
                        language="sql"
                    )

        else:
            st.markdown(message["content"])


# Chat input
if question := st.chat_input("Ask your question..."):

    # Add user message to chat history
    st.session_state.messages.append(
        {
            "role": "user",
            "content": question
        }
    )

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):

        with st.spinner("Processing..."):

            # Send the current session_id (if any) to the API
            current_session_id = st.session_state.session_id

            result = send_question(
                question,
                current_session_id
            )

        if result and result.get("success", False):

            # Check if session_id is returned in the response
            if "session_id" in result and result["session_id"]:

                st.session_state.session_id = result["session_id"]

                print(
                    f"Session ID updated from API: "
                    f"{st.session_state.session_id}"
                )

            else:

                # If no session_id returned, keep the existing one
                print(
                    "Warning: No session_id returned from API. "
                    "Keeping existing session."
                )

            # Display the answer (Markdown)
            st.markdown(
                result["answer"],
                unsafe_allow_html=True
            )

            # Display data table if any
            if result.get("rows"):

                df = pd.DataFrame(result["rows"])

                st.dataframe(
                    df,
                    width='stretch'
                )

            # Display SQL query if any
            if result.get("sql"):

                with st.expander("View SQL Query"):
                    st.code(
                        result["sql"],
                        language="sql"
                    )

            # Store assistant's response in session history
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": result["answer"],
                    "rows": result.get("rows", []),
                    "sql": result.get("sql", "")
                }
            )

        else:

            # Handle errors
            error_msg = (
                result.get("error", "Unknown error")
                if result
                else "API connection failed"
            )

            st.error(
                f"Error: {error_msg}"
            )

            if result and result.get("sql"):
                st.code(
                    result["sql"],
                    language="sql"
                )

            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": f"Error: {error_msg}"
                }
            )
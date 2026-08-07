# src/web/app.py

import os
import streamlit as st
import requests
import pandas as pd
from typing import Optional
from dotenv import load_dotenv

load_dotenv()  # بارگذاری متغیرهای محیطی از فایل .env

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

def send_question(question: str) -> Optional[dict]:
    try:
        response = requests.post(
            f"{API_URL}/ask",
            json={
                "question": question,
                "session_id": st.session_state.session_id
            },
            timeout=30
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

def format_rows_as_dataframe(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)

st.title("Text-to-SQL Agent")
st.markdown("Ask your question in natural language and get SQL results.")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            st.markdown(message["content"])
            if "rows" in message and message["rows"]:
                df = pd.DataFrame(message["rows"])
                st.dataframe(df, use_container_width=True)
            if "sql" in message and message["sql"]:
                with st.expander("View SQL Query"):
                    st.code(message["sql"], language="sql")
        else:
            st.markdown(message["content"])

if question := st.chat_input("Ask your question..."):
    st.session_state.messages.append({"role": "user", "content": question})
    
    with st.chat_message("user"):
        st.markdown(question)
    
    with st.chat_message("assistant"):
        with st.spinner("Processing..."):
            result = send_question(question)
        
        if result and result.get("success", False):
            st.markdown(result["answer"])
            
            if result.get("rows"):
                df = pd.DataFrame(result["rows"])
                st.dataframe(df, use_container_width=True)
            
            if result.get("sql"):
                with st.expander("View SQL Query"):
                    st.code(result["sql"], language="sql")
            
            st.session_state.messages.append({
                "role": "assistant",
                "content": result["answer"],
                "rows": result.get("rows", []),
                "sql": result.get("sql", "")
            })
            
            if "session_id" in result:
                st.session_state.session_id = result["session_id"]
        
        else:
            error_msg = result.get("error", "Unknown error") if result else "API connection failed"
            st.error(f"Error: {error_msg}")
            if result and result.get("sql"):
                st.code(result["sql"], language="sql")
            
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"Error: {error_msg}"
            })

if st.sidebar.button("Clear History"):
    st.session_state.messages = []
    st.session_state.session_id = None
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### Information")
st.sidebar.markdown(f"**Messages:** {len(st.session_state.messages)}")
if st.session_state.session_id:
    st.sidebar.markdown(f"**Session ID:** {st.session_state.session_id}")
import streamlit as st

st.set_page_config(layout="wide", initial_sidebar_state="expanded")

st.sidebar.title("Test Menu")
st.sidebar.radio("Pilih:", ["Menu 1", "Menu 2"])

st.title("Halaman Utama Berjalan Normal")

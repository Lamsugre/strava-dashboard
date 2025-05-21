import streamlit as st
import requests
import pandas as pd

st.title("🏃 Mes dernières activités Strava")

activities = [{"name": "Test run", "distance": 5000, "elapsed_time": 1600, "start_date_local": "2024-01-01T09:00:00"}]

st.subheader("🛠️ Données brutes reçues :")
st.json(activities)

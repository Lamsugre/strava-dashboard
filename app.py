# 📈 Affichage
import streamlit as st
import requests
import pandas as pd
import datetime

st.title("🏃 Mes dernières activités Strava")

st.subheader("🛠️ Données brutes reçues :")
st.json(activities)  # ← AJOUTE cette ligne

if isinstance(activities, list) and activities:
    df = pd.DataFrame([{
        "Nom": act["name"],
        "Distance (km)": round(act["distance"] / 1000, 2),
        "Durée (min)": round(act["elapsed_time"] / 60, 1),
        "Allure (min/km)": round((act["elapsed_time"] / 60) / (act["distance"] / 1000), 2),
        "Date": act["start_date_local"][:10]
    } for act in activities])
    
    st.dataframe(df)
else:
    st.error("❌ L'API Strava n'a pas renvoyé une liste d'activités valides.")

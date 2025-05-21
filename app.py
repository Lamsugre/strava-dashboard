import streamlit as st
import requests
import pandas as pd
import datetime
import os

# 📥 Lecture des secrets
client_id = st.secrets["STRAVA_CLIENT_ID"]
client_secret = st.secrets["STRAVA_CLIENT_SECRET"]
refresh_token = st.secrets["STRAVA_REFRESH_TOKEN"]

# 🔁 Rafraîchir le token d'accès
def refresh_access_token():
    url = "https://www.strava.com/oauth/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    res = requests.post(url, data=payload)
    return res.json()["access_token"]

access_token = refresh_access_token()

# 📊 Récupérer les dernières activités
def get_strava_activities(access_token, num_activities=5):
    url = f"https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"per_page": num_activities, "page": 1}
    res = requests.get(url, headers=headers, params=params)
    return res.json()

# 📋 Charger les données
activities = get_strava_activities(access_token)

# 📈 Affichage
st.title("🏃 Mes dernières activités Strava")

if activities:
    df = pd.DataFrame([{
        "Nom": act["name"],
        "Distance (km)": round(act["distance"] / 1000, 2),
        "Durée (min)": round(act["elapsed_time"] / 60, 1),
        "Allure (min/km)": round((act["elapsed_time"] / 60) / (act["distance"] / 1000), 2),
        "Date": act["start_date_local"][:10]
    } for act in activities])
    
    st.dataframe(df)
else:
    st.warning("Aucune activité trouvée.")

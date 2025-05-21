import streamlit as st
import requests
import pandas as pd
import datetime

# 🧪 Titre de l'application
st.title("🏃 Mes dernières activités Strava")

# 🔐 Récupération des secrets Streamlit (stockés dans Settings > Secrets)
client_id = st.secrets["STRAVA_CLIENT_ID"]
client_secret = st.secrets["STRAVA_CLIENT_SECRET"]
refresh_token = st.secrets["STRAVA_REFRESH_TOKEN"]

# 🔁 Fonction pour rafraîchir le token d'accès
def refresh_access_token():
    url = "https://www.strava.com/oauth/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    res = requests.post(url, data=payload)
    res.raise_for_status()
    return res.json()["access_token"]

# 📊 Fonction pour récupérer les activités via l'API
def get_strava_activities(access_token, num_activities=10):
    url = f"https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"per_page": num_activities, "page": 1}
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    return res.json()

# 🚀 Récupération des données
try:
    access_token = refresh_access_token()
    activities = get_strava_activities(access_token)

    # ✅ Affichage des données si tout est OK
    if isinstance(activities, list) and activities:
        df = pd.DataFrame([{
            "Nom": act.get("name", "—"),
            "Distance (km)": round(act["distance"] / 1000, 2),
            "Durée (min)": round(act["elapsed_time"] / 60, 1),
            "Allure (min/km)": round((act["elapsed_time"] / 60) / (act["distance"] / 1000), 2) if act["distance"] > 0 else None,
            "Date": act["start_date_local"][:10]
        } for act in activities])

        st.subheader("📋 Tableau des activités")
        st.dataframe(df)
    else:
        st.warning("Aucune activité Strava trouvée.")

except Exception as e:
    st.error("❌ Une erreur est survenue lors de la récupération des données.")
    st.exception(e)

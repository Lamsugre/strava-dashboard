import streamlit as st
import requests
import pandas as pd
import datetime
import altair as alt

st.title("🏃 Mon tableau de bord Strava - AI Coach X")

client_id = st.secrets["STRAVA_CLIENT_ID"]
client_secret = st.secrets["STRAVA_CLIENT_SECRET"]
refresh_token = st.secrets["STRAVA_REFRESH_TOKEN"]

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

def get_strava_activities(access_token, num_activities=50):
    url = f"https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"per_page": num_activities, "page": 1}
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    return res.json()

@st.cache_data(ttl=1800)
def get_activities_cached():
    access_token = refresh_access_token()
    return get_strava_activities(access_token)

try:
    st.subheader("🔄 Mise à jour manuelle des données")

    if st.button("📥 Actualiser mes données Strava"):
        try:
            activities = get_activities_cached()
            st.success("✅ Données mises à jour !")
        except Exception as e:
            st.error("❌ Erreur lors de la récupération des données.")
            st.exception(e)
    else:
        st.info("🕒 Cliquez sur le bouton ci-dessus pour charger vos données.")
        activities = None

    if activities and isinstance(activities, list):
        df = pd.DataFrame([{
            "Nom": act.get("name", "—"),
            "Distance (km)": round(act["distance"] / 1000, 2),
            "Durée (min)": round(act["elapsed_time"] / 60, 1),
            "Allure (min/km)": round((act["elapsed_time"] / 60) / (act["distance"] / 1000), 2) if act["distance"] > 0 else None,
            "Date": act["start_date_local"][:10],
            "Type": act.get("type", "—")
        } for act in activities])

        df["Date"] = pd.to_datetime(df["Date"])
        df["Semaine"] = df["Date"].dt.strftime("%Y-%U")

        st.subheader("📋 Filtrer les activités")
        types_disponibles = df["Type"].unique().tolist()
        type_choisi = st.selectbox("Type d'activité", ["Toutes"] + types_disponibles)

        if type_choisi != "Toutes":
            df = df[df["Type"] == type_choisi]

        date_range = st.date_input("Période", [df["Date"].min(), df["Date"].max()])
        if len(date_range) == 2:
            df = df[(df["Date"] >= date_range[0]) & (df["Date"] <= date_range[1])]

        st.subheader("📋 Tableau des activités filtrées")
        st.dataframe(df)

        st.subheader("📈 Volume hebdomadaire & Allure moyenne")
        df_weekly = df.groupby("Semaine").agg({
            "Distance (km)": "sum",
            "Durée (min)": "sum"
        }).reset_index()
        df_weekly["Allure (min/km)"] = df_weekly["Durée (min)"] / df_weekly["Distance (km)"]

        bar_chart = alt.Chart(df_weekly).mark_bar(color="#1f77b4").encode(
            x=alt.X("Semaine:O", title="Semaine"),
            y=alt.Y("Distance (km):Q", title="Distance (km)"),
            tooltip=["Semaine", "Distance (km)", "Allure (min/km)"]
        )

        line_chart = alt.Chart(df_weekly).mark_line(color="orange", point=True).encode(
            x="Semaine:O",
            y=alt.Y("Allure (min/km):Q", title="Allure (min/km)", axis=alt.Axis(titleColor="orange")),
            tooltip=["Allure (min/km)"]
        )

        chart = alt.layer(bar_chart, line_chart).resolve_scale(y='independent').properties(
            width=700, height=400
        )

        st.altair_chart(chart)

        st.subheader("📊 Statistiques de la semaine la plus récente")
        if not df_weekly.empty:
            last_week = df_weekly.iloc[-1]
            st.metric("Distance", f"{last_week['Distance (km)']:.1f} km")
            st.metric("Allure moyenne", f"{last_week['Allure (min/km)']:.2f} min/km")
            st.metric("Temps total", f"{last_week['Durée (min)']:.0f} min")

    elif activities is not None:
        st.warning("Aucune activité Strava trouvée.")

except Exception as e:
    st.error("❌ Une erreur est survenue lors de l'exécution.")
    st.exception(e)

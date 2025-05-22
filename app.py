import streamlit as st
import requests
import pandas as pd
import datetime
import altair as alt
import json
import os
import openai

st.title("🏃 Dashbord - AI Coach X")

client_id = st.secrets["STRAVA_CLIENT_ID"]
client_secret = st.secrets["STRAVA_CLIENT_SECRET"]
refresh_token = st.secrets["STRAVA_REFRESH_TOKEN"]
openai.api_key = st.secrets["OPENAI_API_KEY"]

PLAN_PATH = "plan_semi_vincennes_2025.json"

if os.path.exists(PLAN_PATH):
    with open(PLAN_PATH, "r", encoding="utf-8") as f:
        plan_data = json.load(f)
    df_plan = pd.DataFrame(plan_data)
    df_plan["date"] = pd.to_datetime(df_plan["date"])
else:
    df_plan = pd.DataFrame()

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

def appel_chatgpt_conseil(prompt, df_activites, df_plan):
    from openai import OpenAI
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    plan_resume = df_plan.head(3).to_string(index=False)
    activites_resume = df_activites.head(3).to_string(index=False)
    system_msg = "Tu es un coach sportif intelligent. Rédige un retour clair, synthétique et utile en te basant sur les dernières performances Strava et les séances prévues."
    user_msg = f"""Voici les séances prévues:
{plan_resume}

Voici les séances réalisées:
{activites_resume}

Question: {prompt}"""
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg}
        ],
        temperature=0.7
    )
    return response.choices[0].message.content

# 🔄 Récupération ou actualisation des activités
activities = st.session_state.get("activities", None)

st.subheader("📅 Actualisation des données")
if st.button("📥 Actualiser mes données Strava"):
    try:
        activities = get_activities_cached()
        st.session_state["activities"] = activities
        st.success("Données Strava mises à jour avec succès.")
    except Exception as e:
        st.error("Erreur lors de la récupération des données.")
        st.exception(e)

# Affichage du tableau et du plan si les données sont disponibles
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
    df["Date_affichée"] = df["Date"].dt.strftime("%d/%m/%Y")
    df["Semaine"] = df["Date"].dt.strftime("%Y-%U")

    st.subheader("📋 Tableau des activités")
    st.dataframe(df.drop(columns="Date").rename(columns={"Date_affichée": "Date"}))

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

    st.subheader("🗓️ Mon plan d'entraînement")
    today = datetime.datetime.now().date()
    plan_du_jour = df_plan[df_plan["date"] >= pd.to_datetime(today)].head(6)
    plan_du_jour_display = plan_du_jour.copy()
    plan_du_jour_display["date"] = plan_du_jour_display["date"].dt.strftime("%d/%m/%Y")
    plan_du_jour_display["phases"] = plan_du_jour_display["phases"].apply(
        lambda p: " | ".join([f"{ph.get('nom', '')}: {ph.get('contenu', str(ph.get('durée_min', '')) + ' min')}" for ph in p])
    )
    st.dataframe(plan_du_jour_display)
    st.subheader("🧩 Détail des séances à venir")
    for _, row in plan_du_jour.iterrows():
        with st.expander(f"{row['date'].strftime('%d/%m/%Y')} - {row['type'].capitalize()} ({row['jour']})"):
            for phase in row['phases']:
                nom = phase.get("nom", "")
                contenu = phase.get("contenu") or f"{phase.get('durée_min', '')} min"
                st.markdown(f"**{nom.capitalize()}** → {contenu}")

with st.sidebar:
    st.subheader("🧠 Coach IA : pose une question")
    if activities and isinstance(activities, list):
        df = pd.DataFrame([{
            "Nom": act.get("name", "—"),
            "Distance (km)": round(act["distance"] / 1000, 2),
            "Durée (min)": round(act["elapsed_time"] / 60, 1),
            "Allure (min/km)": round((act["elapsed_time"] / 60) / (act["distance"] / 1000), 2) if act["distance"] > 0 else None,
            "Date": act["start_date_local"][:10],
            "Type": act.get("type", "—")
        } for act in activities])
        question = st.text_area("Ta question au coach :", key="chat_input", height=120)
        if question:
            reponse = appel_chatgpt_conseil(question, df, df_plan)
            st.markdown("---")
            st.markdown("**Réponse du coach :**")
            st.markdown(reponse)
    else:
        st.markdown("⚠️ Données Strava non disponibles.")

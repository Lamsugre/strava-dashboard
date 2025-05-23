import streamlit as st
import requests
import pandas as pd
import datetime
import altair as alt
import json
import os
import openai
import base64
from github import Github

# 🔐 Protection par mot de passe simple
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
        else:
            st.session_state["password_correct"] = False
            st.error("Mot de passe incorrect.")

    if "password_correct" not in st.session_state:
        st.text_input("Mot de passe", type="password", on_change=password_entered, key="password")
        st.stop()
    elif not st.session_state["password_correct"]:
        st.text_input("Mot de passe", type="password", on_change=password_entered, key="password")
        st.stop()

check_password()

st.title("🏃 Dashbord - AI Coach X")

client_id = st.secrets["STRAVA_CLIENT_ID"]
client_secret = st.secrets["STRAVA_CLIENT_SECRET"]
refresh_token = st.secrets["STRAVA_REFRESH_TOKEN"]
openai.api_key = st.secrets["OPENAI_API_KEY"]
github_token = st.secrets["GITHUB_TOKEN"]
github_repo = st.secrets["GITHUB_REPO"]

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
    plan_resume = df_plan.head(3).to_string(index=False)
    activites_resume = df_activites.head(3).to_string(index=False)
    system_msg = "Tu es un coach sportif intelligent. Rédige un retour clair, synthétique et utile en te basant sur les dernières performances Strava et les séances prévues."
    user_msg = f"""Voici les séances prévues:\n{plan_resume}\n\nVoici les séances réalisées:\n{activites_resume}\n\nQuestion: {prompt}"""
    client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg}
        ],
        temperature=0.7
    )
    return response.choices[0].message.content

def commit_to_github(updated_text):
    g = Github(github_token)
    repo = g.get_repo(github_repo)
    file = repo.get_contents(PLAN_PATH)
    encoded_content = base64.b64decode(file.content).decode("utf-8")
    old_sha = file.sha
    repo.update_file(
        path=PLAN_PATH,
        message="🟢 Mise à jour automatique du plan via IA",
        content=updated_text,
        sha=old_sha
    )

# Page selector
page = st.sidebar.radio("Choisir une vue", ["Tableau général", "Analyse Fractionné"])

activities = st.session_state.get("activities", None)

st.subheader(" Actualisation des données")
if st.button(" Actualiser mes données Strava"):
    try:
        activities = get_activities_cached()
        st.session_state["activities"] = activities
        st.success("Données Strava mises à jour avec succès.")
    except Exception as e:
        st.error("Erreur lors de la récupération des données.")
        st.exception(e)

if activities and isinstance(activities, list):
    df = pd.DataFrame([{
        "Nom": act.get("name", "—"),
        "Distance (km)": round(act["distance"] / 1000, 2),
        "Durée (min)": round(act["elapsed_time"] / 60, 1),
        "Allure (min/km)": round((act["elapsed_time"] / 60) / (act["distance"] / 1000), 2) if act["distance"] > 0 else None,
        "FC Moyenne": act.get("average_heartrate"),
        "FC Max": act.get("max_heartrate"),
        "Date": act["start_date_local"][:10],
        "Type": act.get("type", "—")
    } for act in activities])

    df["Date"] = pd.to_datetime(df["Date"])
    df["Date_affichée"] = df["Date"].dt.strftime("%d/%m/%Y")
    df["Semaine"] = df["Date"].dt.strftime("%Y-%U")

    if page == "🏠 Tableau général":
        st.subheader("📋 Tableau des activités")
        types_disponibles = df["Type"].unique().tolist()
        type_choisi = st.selectbox("Filtrer par type d'activité", ["Toutes"] + types_disponibles, key="type_filter")
        if type_choisi != "Toutes":
            df = df[df["Type"] == type_choisi]
        st.dataframe(df.drop(columns="Date").rename(columns={"Date_affichée": "Date"}))

    elif page == "💥 Analyse Fractionné":
        st.subheader("💥 Analyse des séances de fractionné")
        df_intervals = df[df["Nom"].str.contains("fractionné|VMA|10x|interv", case=False, na=False)]
        st.dataframe(df_intervals[["Date_affichée", "Nom", "Distance (km)", "Allure (min/km)", "FC Moyenne", "FC Max"]])

with st.sidebar:
    st.subheader("🧠 Coach IA : pose une question")
    if activities and isinstance(activities, list):
        question = st.text_area("Ta question au coach :", key="chat_input", height=120)
        if question:
            reponse = appel_chatgpt_conseil(question, df, df_plan)
            st.markdown("---")
            st.markdown("**Réponse du coach :**")
            st.markdown(reponse)
    else:
        st.markdown("⚠️ Données Strava non disponibles.")

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
    activities = res.json()

    detailed_activities = []
    for act in activities:
        activity_id = act["id"]
        detail_url = f"https://www.strava.com/api/v3/activities/{activity_id}"
        detail_res = requests.get(detail_url, headers=headers)
        detail_res.raise_for_status()
        detail_data = detail_res.json()
        act["description"] = detail_data.get("description", "")
        detailed_activities.append(act)

    return detailed_activities

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
        message="Mise a jour automatique du plan via IA",
        content=updated_text,
        sha=old_sha
    )

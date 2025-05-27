import streamlit as st
import requests
import pandas as pd
import datetime
import altair as alt
import json
import os
import openai
import base64
from io import BytesIO

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
CACHE_PARQUET_PATH = "data/strava_data_cache.parquet"


if os.path.exists(PLAN_PATH):
    with open(PLAN_PATH, "r", encoding="utf-8") as f:
        plan_data = json.load(f)
    weeks = plan_data.get("weeks", [])
    records = []
    for week in weeks:
        for session in week.get("sessions", []):
            record = {
                "week": week.get("week", ""),
                "day": session.get("day", ""),
                "name": session.get("name", ""),
                "type": session.get("type", ""),
                "duration_min": session.get("duration_min", ""),
                "distance_km": session.get("distance_km", ""),
                "details": json.dumps(session.get("details", {}))
            }
            records.append(record)
    df_plan = pd.DataFrame(records)
else:
    df_plan = pd.DataFrame()
def mettre_a_jour_et_commit_cache_parquet(new_activities_df):
    if os.path.exists(CACHE_PARQUET_PATH):
        df_cache = pd.read_parquet(CACHE_PARQUET_PATH)
        ids_existants = set(df_cache["id"].astype(str))
        df_nouvelles = new_activities_df[~new_activities_df["id"].astype(str).isin(ids_existants)]
        df_final = pd.concat([df_cache, df_nouvelles], ignore_index=True)
    else:
        df_final = new_activities_df

    # Écriture en local
    df_final.to_parquet(CACHE_PARQUET_PATH, index=False)

    # Préparation du buffer pour GitHub
    buffer = BytesIO()
    df_final.to_parquet(buffer, index=False)
    buffer.seek(0)
    content_encoded = base64.b64encode(buffer.read()).decode('utf-8')

    # Commit sur GitHub
    g = Github(github_token)
    repo = g.get_repo(github_repo)
    path_remote = CACHE_PARQUET_PATH  # Exemple: "data/strava_data_cache.parquet"

    try:
        file = repo.get_contents(path_remote)
        repo.update_file(
            path=path_remote,
            message="Mise à jour automatique du cache Strava",
            content=content_encoded,
            sha=file.sha
        )
    except Exception as e:
        # Création initiale si le fichier n'existe pas
        repo.create_file(
            path=path_remote,
            message="Ajout initial du cache Strava",
            content=content_encoded
        )

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

def get_strava_activities(access_token, num_activities=50, max_detailed=5):
    url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"per_page": num_activities, "page": 1}
    res = requests.get(url, headers=headers, params=params)

    if res.status_code == 429:
        st.warning("⏱️ Tu as atteint la limite de requêtes Strava. Réessaie dans quelques minutes.")
        return []

    res.raise_for_status()
    activities = res.json()

    detailed_activities = []
    for i, act in enumerate(activities):
        if i >= max_detailed:
            act["description"] = ""
            detailed_activities.append(act)
            continue

        activity_id = act["id"]
        detail_url = f"https://www.strava.com/api/v3/activities/{activity_id}"
        detail_res = requests.get(detail_url, headers=headers)
        if detail_res.status_code == 429:
            st.warning("Limite de requêtes atteinte pendant les détails. Interruption du chargement détaillé.")
            break
        detail_res.raise_for_status()
        detail_data = detail_res.json()
        act["description"] = detail_data.get("description", "")
        detailed_activities.append(act)

    return detailed_activities
def construire_dataframe_activites_complet(activities, access_token):
    """
    Construit un DataFrame enrichi avec les données classiques + fréquence cardiaque par minute.
    """
    rows = []
    headers = {"Authorization": f"Bearer {access_token}"}

    for act in activities:
        row = {
            "id": act.get("id"),
            "Nom": act.get("name", "—"),
            "Distance (km)": round(act.get("distance", 0) / 1000, 2),
            "Durée (min)": round(act.get("elapsed_time", 0) / 60, 1),
            "Allure (min/km)": round((act.get("elapsed_time", 0) / 60) / (act.get("distance", 1) / 1000), 2) if act.get("distance", 0) > 0 else None,
            "FC Moyenne": act.get("average_heartrate"),
            "FC Max": act.get("max_heartrate"),
            "Date": act.get("start_date_local", "")[:10],
            "Type": act.get("type", "—"),
            "Description": act.get("description", "")
        }

        # Appel de l'API Strava pour récupérer le stream FC + temps
        stream_url = f"https://www.strava.com/api/v3/activities/{act['id']}/streams"
        params = {"keys": "heartrate,time", "key_by_type": "true"}
        try:
            stream_res = requests.get(stream_url, headers=headers, params=params)
            if stream_res.status_code == 200:
                stream_data = stream_res.json()
                row["FC Stream"] = stream_data.get("heartrate", {}).get("data", [])
                row["Temps Stream"] = stream_data.get("time", {}).get("data", [])
            else:
                row["FC Stream"] = []
                row["Temps Stream"] = []
        except Exception as e:
            row["FC Stream"] = []
            row["Temps Stream"] = []

        rows.append(row)

    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    df["Date_affichée"] = df["Date"].dt.strftime("%d/%m/%Y")
    df["Semaine"] = df["Date"].dt.strftime("%Y-%U")
    return df

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
def appel_chatgpt_conseil(question, df_activities, df_plan):
    import openai

    # Préparer le contexte des données
    resume_activites = df_activities[["Date_affichée", "Nom", "Distance (km)", "Allure (min/km)", "FC Moyenne"]].tail(5).to_string(index=False)
    resume_plan = df_plan[["week", "day", "name", "type", "distance_km"]].head(5).to_string(index=False)

    prompt = f"""Tu es un coach de course à pied expérimenté.
Voici un résumé des dernières activités de l'utilisateur :
{resume_activites}

Voici les prochaines séances de son plan :
{resume_plan}

Voici sa question :
{question}

Réponds de manière claire, utile et personnalisée.
"""

    # Appel à l’API OpenAI
    from openai import OpenAI
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "Tu es un coach sportif expert en préparation marathon."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.6,
    )   

    return response.choices[0].message.content
@st.cache_data(ttl=1800)
def get_activities_cached():
    access_token = refresh_access_token()
    return get_strava_activities(access_token)
activities = st.session_state.get("activities", None)
with st.sidebar:
    st.subheader("🧠 Coach IA : pose une question")
    question = st.text_area("Ta question au coach :", key="chat_input", height=120)
    if st.button("💬 Envoyer au coach IA"):
        if activities and isinstance(activities, list):
            try:
                df = pd.DataFrame([{
                    "Nom": act.get("name", "—"),
                    "Distance (km)": round(act["distance"] / 1000, 2),
                    "Durée (min)": round(act["elapsed_time"] / 60, 1),
                    "Allure (min/km)": round((act["elapsed_time"] / 60) / (act["distance"] / 1000), 2) if act["distance"] > 0 else None,
                    "FC Moyenne": act.get("average_heartrate"),
                    "FC Max": act.get("max_heartrate"),
                    "Date": act["start_date_local"][:10],
                    "Type": act.get("type", "—"),
                    "Description": act.get("description", "")
                } for act in activities])

                df["Date"] = pd.to_datetime(df["Date"])
                df["Date_affichée"] = df["Date"].dt.strftime("%d/%m/%Y")
                df["Semaine"] = df["Date"].dt.strftime("%Y-%U")

                reponse = appel_chatgpt_conseil(question.strip(), df, df_plan)
                st.markdown("---")
                st.markdown("**Réponse du coach :**")
                st.markdown(reponse)
            except Exception as e:
                st.error("❌ Erreur dans l’appel à l’IA.")
                st.exception(e)
        else:
            st.warning("⚠️ Les données Strava ne sont pas encore chargées. Actualise les données avant de poser une question.")
# Page selector
page = st.sidebar.radio("📂 Choisir une vue", ["🏠 Tableau général", "💥 Analyse Fractionné"])

activities = st.session_state.get("activities", None)

st.subheader("📅 Actualisation des données")

if st.button("📥 Actualiser mes données Strava"):
    try:
        activities = get_activities_cached()
        access_token = refresh_access_token()
        df_nouvelles = construire_dataframe_activites_complet(activities, access_token)

        mettre_a_jour_et_commit_cache_parquet(df_nouvelles)

        st.session_state["activities"] = activities
        st.success("Données mises à jour.")
    except Exception as e:
        st.error("Erreur pendant la mise à jour.")
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
        "Type": act.get("type", "—"),
        "Description": act.get("description", "")
    } for act in activities])

    df["Date"] = pd.to_datetime(df["Date"])
    df["Date_affichée"] = df["Date"].dt.strftime("%d/%m/%Y")
    df["Semaine"] = df["Date"].dt.strftime("%Y-%U")

    if page == "🏠 Tableau général":
        # Existing content preserved
        st.subheader("📋 Tableau des activités")
        types_disponibles = df["Type"].unique().tolist()
        type_choisi = st.selectbox("Filtrer par type d'activité", ["Toutes"] + types_disponibles, key="type_filter")
        if type_choisi != "Toutes":
            df = df[df["Type"] == type_choisi]
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
        
    
        st.subheader("📅 Prochaines séances du plan")
        if not df_plan.empty:
            st.dataframe(df_plan.head(6))
        else:
            st.info("Aucune donnée de plan disponible.")
    
        st.markdown("---")
        st.subheader("🛠️ Modifier mon plan avec l'IA")
        edit_prompt = st.text_area("Décris le changement souhaité", key="edit_prompt")

        st.markdown("### 🗓️ Les 4 prochaines séances")
        prochaines = df_plan.head(4)

        for i, row in prochaines.iterrows():
            with st.expander(f"🏃 {row['day']} – {row['name']}"):
                st.markdown(f"**Type :** {row['type']}")
                st.markdown(f"**Durée :** {row['duration_min']} min")
                st.markdown(f"**Distance :** {row['distance_km']} km")
                st.markdown("**Détails :**")
                try:
                    parsed_details = json.loads(row["details"])
                    for k, v in parsed_details.items():
                        st.markdown(f"- **{k}** : {v}")
                except:
                    st.markdown(row["details"])
    
        if st.button("💬 Générer une proposition de modification IA"):
            try:
                instruction_modif = f"Voici le plan actuel:\n{df_plan.to_string(index=False)}\n\nVoici la demande:\n{edit_prompt}\n\nPropose uniquement UNE séance modifiée sous forme d'un objet JSON valide (ne réponds que par le JSON sans explication)."
                client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "Tu es un assistant expert en entraînement de course à pied. Tu modifies le plan d'entraînement au format JSON."},
                        {"role": "user", "content": instruction_modif}
                    ],
                    temperature=0.4
                )
                json_proposal = response.choices[0].message.content
                st.session_state["last_json_modif"] = json_proposal
                st.code(json_proposal, language="json")
            except Exception as e:
                st.error("Erreur lors de la génération par l'IA.")
                st.exception(e)

            if "last_json_modif" in st.session_state and st.button("✅ Appliquer cette modification au fichier"):
                try:
                    new_obj = json.loads(st.session_state["last_json_modif"])
                    df_plan["date"] = pd.to_datetime(df_plan["date"])
                    df_plan.set_index("date", inplace=True)
                    new_date = pd.to_datetime(new_obj["date"])
                    df_plan.loc[new_date] = new_obj
                    df_plan.reset_index(inplace=True)
                    df_plan.sort_values(by="date", inplace=True)

                    final_text = json.dumps(df_plan.to_dict(orient="records"), indent=2, ensure_ascii=False, default=str)
                    with open(PLAN_PATH, "w", encoding="utf-8") as f:
                        f.write(final_text)
                    commit_to_github(final_text)
                    st.success("✅ Plan mis à jour et synchronisé avec GitHub.")
                    st.rerun()
                except Exception as e:
                    st.error("❌ Erreur lors de l'application de la modification.")
                    st.exception(e)

    elif page == "💥 Analyse Fractionné":
        st.subheader("🧩 Laps de la séance du 19/05/2025")

        # Appel API Strava pour récupérer les laps
        activity_id = "14527571757"
        access_token = refresh_access_token()
        url_laps = f"https://www.strava.com/api/v3/activities/{activity_id}/laps"
        headers = {"Authorization": f"Bearer {access_token}"}
        res = requests.get(url_laps, headers=headers)
        
        if res.status_code == 200:
            laps_data = res.json()
            df_laps = pd.DataFrame([{
                "Lap": i + 1,
                "Type": lap.get("name", "—"),
                "Distance (km)": round(lap["distance"] / 1000, 2),
                "Temps (min)": round(lap["elapsed_time"] / 60, 1),
                "FC Moy": lap.get("average_heartrate"),
                "FC Max": lap.get("max_heartrate"),
                "Allure (min/km)": round((lap["elapsed_time"] / 60) / (lap["distance"] / 1000), 2) if lap["distance"] > 0 else None
            } for i, lap in enumerate(laps_data)])

            st.dataframe(df_laps)
        else:
            st.error("Impossible de récupérer les laps depuis l’API Strava.")
            st.text(res.text)
            
            df_tempo = df[df["Description"].str.contains("Tempo", case=False, na=False)]
            if not df_tempo.empty:
                st.dataframe(df_tempo[["Date_affichée", "Nom", "Description", "Distance (km)", "Allure (min/km)", "FC Moyenne", "FC Max"]])
            else:
                st.info("Aucune séance 'tempo' détectée dans les descriptions Strava.")

def charger_cache_parquet():
    if os.path.exists(CACHE_PARQUET_PATH):
        return pd.read_parquet(CACHE_PARQUET_PATH)
    else:
        return pd.DataFrame()


def mettre_a_jour_et_commit_cache_parquet(new_activities_df):
    if os.path.exists(CACHE_PARQUET_PATH):
        df_cache = pd.read_parquet(CACHE_PARQUET_PATH)
        ids_existants = set(df_cache["id"].astype(str))
        df_nouvelles = new_activities_df[~new_activities_df["id"].astype(str).isin(ids_existants)]
        df_final = pd.concat([df_cache, df_nouvelles], ignore_index=True)
    else:
        df_final = new_activities_df

    # Écriture locale
    df_final.to_parquet(CACHE_PARQUET_PATH, index=False)

    # Préparation du fichier binaire pour GitHub
    buffer = BytesIO()
    df_final.to_parquet(buffer, index=False)
    buffer.seek(0)
    content_encoded = base64.b64encode(buffer.read()).decode('utf-8')

    # Commit GitHub
    g = Github(github_token)
    repo = g.get_repo(github_repo)
    path_remote = CACHE_PARQUET_PATH

    try:
        file = repo.get_contents(path_remote)
        repo.update_file(
            path=path_remote,
            message="🔄 Mise à jour du cache Strava (parquet)",
            content=content_encoded,
            sha=file.sha
        )
    except:
        repo.create_file(
            path=path_remote,
            message="✨ Création initiale du cache Strava (parquet)",
            content=content_encoded
        )


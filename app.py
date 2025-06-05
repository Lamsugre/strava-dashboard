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
PLAN_YEAR = 2025


def minutes_to_mmss(minutes: float) -> str:
    """Convert minutes per km to mm:ss string."""
    if minutes is None or pd.isna(minutes):
        return ""
    total_seconds = int(round(minutes * 60))
    m, s = divmod(total_seconds, 60)
    return f"{m:02d}:{s:02d}"


def seconds_to_mmss(seconds: float) -> str:
    """Convert seconds per km to mm:ss string."""
    if seconds is None or pd.isna(seconds):
        return ""
    total_seconds = int(round(seconds))
    m, s = divmod(total_seconds, 60)
    return f"{m:02d}:{s:02d}"

if os.path.exists(PLAN_PATH):
    with open(PLAN_PATH, "r", encoding="utf-8") as f:
        plan_data = json.load(f)
    weeks = plan_data.get("weeks", [])
    day_map = {day: i for i, day in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}
    records = []
    for week in weeks:
        week_range = week.get("week", "")
        start_str = week_range.split("-")[0].strip()
        try:
            start_date = datetime.datetime.strptime(f"{start_str} {PLAN_YEAR}", "%d %b %Y")
        except ValueError:
            start_date = None
        for session in week.get("sessions", []):
            if start_date and session.get("day") in day_map:
                session_date = (start_date + datetime.timedelta(days=day_map[session.get("day")])).date()
            else:
                session_date = None
            record = {
                "week": week.get("week", ""),
                "day": session.get("day", ""),
                "name": session.get("name", ""),
                "type": session.get("type", ""),
                "duration_min": session.get("duration_min", ""),
                "distance_km": session.get("distance_km", ""),
                "details": json.dumps(session.get("details", {})),
                "date": session_date
            }
            records.append(record)
    df_plan = pd.DataFrame(records)
    if not df_plan.empty:
        df_plan.sort_values(by="date", inplace=True)
else:
    df_plan = pd.DataFrame()

# Corriger le chargement de fichier parquet vide ou non valide
def charger_cache_parquet():
    """Load the local Parquet cache, handling base64 encoded content."""
    if os.path.exists(CACHE_PARQUET_PATH) and os.path.getsize(CACHE_PARQUET_PATH) > 0:
        try:
            try:
                # First attempt: direct parquet read
                return pd.read_parquet(CACHE_PARQUET_PATH)
            except Exception:
                # File may be base64 encoded (when pulled from GitHub)
                with open(CACHE_PARQUET_PATH, "rb") as f:
                    data = base64.b64decode(f.read())
                return pd.read_parquet(BytesIO(data))
        except Exception:
            st.warning("⚠️ Cache invalide. Il sera régénéré.")
    return pd.DataFrame()

# Ne pas redéfinir deux fois cette fonction dans le fichier !
def mettre_a_jour_et_commit_cache_parquet(new_activities_df):
    if os.path.exists(CACHE_PARQUET_PATH):
        try:
            df_cache = pd.read_parquet(CACHE_PARQUET_PATH)
            ids_existants = set(df_cache["id"].astype(str))
            df_nouvelles = new_activities_df[~new_activities_df["id"].astype(str).isin(ids_existants)]
            df_final = pd.concat([df_cache, df_nouvelles], ignore_index=True)
        except:
            df_final = new_activities_df
    else:
        df_final = new_activities_df

    df_final.to_parquet(CACHE_PARQUET_PATH, index=False)

    buffer = BytesIO()
    df_final.to_parquet(buffer, index=False)
    buffer.seek(0)
    content_encoded = base64.b64encode(buffer.read()).decode('utf-8')

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

def get_strava_activities(access_token, num_activities=50, max_detailed=None, existing_ids=None):
    url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"per_page": num_activities, "page": 1}
    res = requests.get(url, headers=headers, params=params)

    if res.status_code == 429:
        st.warning("⏱️ Tu as atteint la limite de requêtes Strava. Réessaie dans quelques minutes.")
        return []

    res.raise_for_status()
    activities = res.json()

    if existing_ids is None:
        existing_ids = set()
    else:
        existing_ids = set(str(i) for i in existing_ids)

    # Remove activities already present in cache
    activities = [a for a in activities if str(a.get("id")) not in existing_ids]

    if max_detailed is None:
        max_detailed = num_activities

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
        pace_min = (
            (act.get("elapsed_time", 0) / 60) / (act.get("distance", 1) / 1000)
            if act.get("distance", 0) > 0
            else None
        )
        row = {
            "id": act.get("id"),
            "Nom": act.get("name", "—"),
            "Distance (km)": round(act.get("distance", 0) / 1000, 2),
            "Durée (min)": round(act.get("elapsed_time", 0) / 60, 1),
            "Allure (min/km)": pace_min,
            "Allure (mm:ss/km)": minutes_to_mmss(pace_min) if pace_min is not None else "",
            "FC Moyenne": act.get("average_heartrate"),
            "FC Max": act.get("max_heartrate"),
            "Date": act.get("start_date_local", "")[:10],
            "Type": act.get("type", "—"),
            "Description": act.get("description", ""),
        }

        # Appel de l'API Strava pour récupérer les streams utiles
        stream_url = f"https://www.strava.com/api/v3/activities/{act['id']}/streams"
        params = {"keys": "heartrate,time,distance,velocity_smooth", "key_by_type": "true"}
        try:
            stream_res = requests.get(stream_url, headers=headers, params=params)
            if stream_res.status_code == 200:
                stream_data = stream_res.json()
                row["FC Stream"] = stream_data.get("heartrate", {}).get("data", [])
                row["Temps Stream"] = stream_data.get("time", {}).get("data", [])
                row["Distance Stream"] = stream_data.get("distance", {}).get("data", [])
                row["Vitesse Stream"] = stream_data.get("velocity_smooth", {}).get("data", [])
            else:
                row["FC Stream"] = []
                row["Temps Stream"] = []
                row["Distance Stream"] = []
                row["Vitesse Stream"] = []
        except Exception as e:
            row["FC Stream"] = []
            row["Temps Stream"] = []
            row["Distance Stream"] = []
            row["Vitesse Stream"] = []

        rows.append(row)

    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    df["Date_affichée"] = df["Date"].dt.strftime("%d/%m/%Y")
    df["Semaine"] = df["Date"].dt.strftime("%Y-%U")
    if "Allure (min/km)" in df.columns:
        df["Allure (s/km)"] = df["Allure (min/km)"] * 60
    return df

def commit_to_github(updated_text):
    g = Github(github_token)
    repo = g.get_repo(github_repo)
    file = repo.get_contents(PLAN_PATH)
    old_sha = file.sha
    repo.update_file(
        path=PLAN_PATH,
        message="Mise a jour automatique du plan via IA",
        content=updated_text,
        sha=old_sha
    )
def appel_chatgpt_conseil(question, df_activities, df_plan):

    # Préparer le contexte des données
    resume_activites = df_activities[["Date_affichée", "Nom", "Distance (km)", "Allure (min/km)", "FC Moyenne"]].tail(5).to_string(index=False)
    resume_plan = df_plan[["week", "day", "name", "type", "distance_km"]].head(5).to_string(index=False)

    prompt = (
        f"Tu es un coach de course à pied expérimenté.\n"
        "Voici un résumé des dernières activités de l'utilisateur :\n"
        f"{resume_activites}\n\n"
        "Voici les prochaines séances de son plan :\n"
        f"{resume_plan}\n\n"
        "Voici sa question :\n"
        f"{question}\n\n"
        "Réponds de manière claire, utile et personnalisée."
    )

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
    df_cache = charger_cache_parquet()
    existing_ids = set(df_cache["id"].astype(str)) if not df_cache.empty else set()

    new_acts = get_strava_activities(
        access_token,
        num_activities=50,
        max_detailed=50,
        existing_ids=existing_ids,
    )

    if new_acts:
        df_new = construire_dataframe_activites_complet(new_acts, access_token)
        mettre_a_jour_et_commit_cache_parquet(df_new)
        df_cache = pd.concat([df_cache, df_new], ignore_index=True)

    if "id" in df_cache.columns:
        df_cache.drop_duplicates(subset="id", inplace=True)
    return df_cache
df_activities = st.session_state.get("df_activities", None)
with st.sidebar:
    st.subheader("🧠 Coach IA : pose une question")
    question = st.text_area("Ta question au coach :", key="chat_input", height=120)
    if st.button("💬 Envoyer au coach IA"):
        if df_activities is not None and not df_activities.empty:
            try:
                reponse = appel_chatgpt_conseil(question.strip(), df_activities, df_plan)
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

if page == "🏠 Tableau général":
    st.subheader("📅 Actualisation des données")

    if st.button("📥 Actualiser mes données Strava"):
        try:
            get_activities_cached.clear()
            df_activities = get_activities_cached()
            st.session_state["df_activities"] = df_activities
            st.success("Données mises à jour.")
        except Exception as e:
            st.error("Erreur pendant la mise à jour.")
            st.exception(e)

if df_activities is not None and not df_activities.empty:
    df = df_activities.copy()
else:
    df_cache = charger_cache_parquet()
    if not df_cache.empty:
        df = df_cache.copy()
        st.info("✅ Données chargées depuis le cache.")
    else:
        st.warning("⚠️ Les données Strava ne sont pas encore chargées.")
        df = pd.DataFrame()

df_cache = charger_cache_parquet()
if 'id' not in df_cache.columns:
    st.warning("❗ Le cache Strava ne contient pas la colonne 'id'. Impossible d'afficher les courbes de fréquence cardiaque.")
    df_cache = pd.DataFrame(columns=['id', 'FC Stream', 'Temps Stream', 'Distance Stream', 'Vitesse Stream'])
# Check if 'id' column exists
if 'id' not in df.columns:
    st.warning("❗ Les données Strava ne contiennent pas la colonne 'id'.")
    df['id'] = None

if not df.empty:
    df["Date"] = pd.to_datetime(df["Date"])
    df["Date_affichée"] = df["Date"].dt.strftime("%d/%m/%Y")
    df["Semaine"] = df["Date"].dt.strftime("%Y-%U")

if page == "🏠 Tableau général":
    st.subheader("📋 Tableau des activités")
    types_disponibles = df["Type"].unique().tolist()
    type_choisi = st.selectbox("Filtrer par type d'activité", ["Toutes"] + types_disponibles, key="type_filter")
    if type_choisi != "Toutes":
        df = df[df["Type"] == type_choisi]
    df_display = df.drop(columns="Date").rename(columns={"Date_affichée": "Date"}).copy()
    if "Allure (min/km)" in df_display.columns:
        df_display["Allure (mm:ss/km)"] = df_display["Allure (min/km)"].apply(minutes_to_mmss)
        df_display.drop(columns=["Allure (min/km)"], inplace=True)
    st.dataframe(df_display)

    st.subheader("📊 Visualiser la fréquence cardiaque")

    # Charge le cache enrichi
    df_cache = charger_cache_parquet()

    # Ensure 'id', 'FC Stream', 'Temps Stream' and 'Distance Stream' columns exist in df_cache
    required_columns = ['id', 'FC Stream', 'Temps Stream', 'Distance Stream']
    for col in required_columns:
        if col not in df_cache.columns:
            st.warning(f"❗ La colonne '{col}' est absente du cache Strava.")
            df_cache[col] = None

    # Perform the merge operation while avoiding duplicate column names
    df_merge = df.merge(
        df_cache[required_columns], on="id", how="left", suffixes=("", "_cache")
    )

    # If the original dataframe possédait déjà les colonnes de stream, on
    # complète les valeurs manquantes avec celles du cache et on supprime les
    # colonnes temporaires
    for col in ["FC Stream", "Temps Stream", "Distance Stream"]:
        cache_col = f"{col}_cache"
        if cache_col in df_merge.columns:
            if col in df_merge.columns:
                df_merge[col] = df_merge[col].combine_first(df_merge[cache_col])
            else:
                df_merge[col] = df_merge[cache_col]
            df_merge.drop(columns=cache_col, inplace=True)

    # Sélecteur
    selected_label = st.selectbox("Choisis une activité :", df_merge["Nom"] + " – " + df_merge["Date_affichée"])
    selected_row = df_merge[df_merge["Nom"] + " – " + df_merge["Date_affichée"] == selected_label]

    if not selected_row.empty:
        fc_stream = selected_row.iloc[0]["FC Stream"]
        time_stream = selected_row.iloc[0]["Temps Stream"]
        distance_stream = selected_row.iloc[0]["Distance Stream"]

        if (
            fc_stream is not None
            and distance_stream is not None
            and len(fc_stream) > 0
            and len(fc_stream) == len(distance_stream)
        ):
            df_graph = pd.DataFrame({
                "Distance (km)": [d / 1000 for d in distance_stream],
                "Fréquence cardiaque (bpm)": fc_stream
            })

            chart = alt.Chart(df_graph).mark_line(color="crimson").encode(
                x=alt.X("Distance (km)", title="Distance (km)", scale=alt.Scale(zero=False)),
                y=alt.Y("Fréquence cardiaque (bpm)", title="FC (bpm)", scale=alt.Scale(zero=False)),
                tooltip=["Distance (km)", "Fréquence cardiaque (bpm)"]
            ).interactive().properties(
                width=700,
                height=300,
                title="Évolution de la FC pendant l'activité"
            )

            st.altair_chart(chart)
        else:
            st.info("Pas de données de fréquence cardiaque disponibles pour cette activité.")
    else:
        st.warning("Sélection invalide.")

    st.subheader("📈 Volume hebdomadaire & Allure moyenne")
    df_weekly = df.groupby("Semaine").agg({
        "Distance (km)": "sum",
        "Durée (min)": "sum"
    }).reset_index()
    df_weekly["Allure (min/km)"] = df_weekly["Durée (min)"] / df_weekly["Distance (km)"]
    df_weekly["Allure (s/km)"] = df_weekly["Allure (min/km)"] * 60
    df_weekly["Allure (mm:ss/km)"] = df_weekly["Allure (min/km)"].apply(minutes_to_mmss)

    bar_chart = (
        alt.Chart(df_weekly)
        .mark_bar(color="#1f77b4")
        .encode(
            x=alt.X("Semaine:O", title="Semaine"),
            y=alt.Y("Distance (km):Q", title="Distance (km)"),
            tooltip=[
                alt.Tooltip("Semaine:N", title="Semaine"),
                alt.Tooltip("Distance (km):Q", title="Distance (km)"),
                alt.Tooltip(field="Allure (mm:ss/km)", type="nominal", title="Allure (mm:ss/km)"),
            ],
        )
    )

    line_chart = (
        alt.Chart(df_weekly)
        .mark_line(color="orange", point=True)
        .encode(
            x="Semaine:O",
            y=alt.Y(
                "`Allure (s/km)`",
                type="quantitative",
                title="Allure (mm:ss/km)",
                axis=alt.Axis(
                    titleColor="orange",
                    labelExpr="timeFormat(datum.value*1000, '%M:%S')",
                ),
                scale=alt.Scale(reverse=True),
            ),
            tooltip=[
                alt.Tooltip(
                    field="Allure (mm:ss/km)",
                    type="nominal",
                    title="Allure (mm:ss/km)",
                )
            ],
        )
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
    today = datetime.date.today()
    prochaines = (
        df_plan[df_plan["date"] >= today]
        .sort_values("date")
        .head(4)
    )

    for i, row in prochaines.iterrows():
        date_str = row['date'].strftime('%d/%m/%Y') if pd.notnull(row['date']) else ''
        with st.expander(f"🏃 {date_str} {row['day']} – {row['name']}"):
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
    st.subheader("🏁 Analyse des séances fractionnées")

    if df.empty:
        st.info("Aucune activité disponible.")
    else:
        df_fractionne = df[df["Description"].str.contains("tempo", case=False, na=False)]
        df_frac_disp = df_fractionne[["Date_affichée", "Nom", "Distance (km)", "Allure (min/km)", "FC Moyenne", "FC Max", "Description"]].rename(columns={"Date_affichée": "Date"}).copy()
        if "Allure (min/km)" in df_frac_disp.columns:
            df_frac_disp["Allure (mm:ss/km)"] = df_frac_disp["Allure (min/km)"].apply(minutes_to_mmss)
            df_frac_disp.drop(columns=["Allure (min/km)"], inplace=True)
        st.dataframe(df_frac_disp)

        if not df_fractionne.empty:
            label_act = st.selectbox(
                "Choisis une séance fractionnée :",
                df_fractionne["Nom"] + " – " + df_fractionne["Date_affichée"]
            )
            selected = df_fractionne[df_fractionne["Nom"] + " – " + df_fractionne["Date_affichée"] == label_act]

            if not selected.empty:
                act_id = selected.iloc[0]["id"]
                access_token = refresh_access_token()
                headers = {"Authorization": f"Bearer {access_token}"}

                # --- Laps
                url_laps = f"https://www.strava.com/api/v3/activities/{act_id}/laps"
                res_laps = requests.get(url_laps, headers=headers)
                if res_laps.status_code == 200:
                    laps_data = res_laps.json()
                    df_laps = pd.DataFrame([
                        {
                            "Lap": i + 1,
                            "Type": lap.get("name", "—"),
                            "Distance (km)": round(lap.get("distance", 0) / 1000, 2),
                            "Temps (min)": round(lap.get("elapsed_time", 0) / 60, 1),
                            "FC Moy": lap.get("average_heartrate"),
                            "Allure (min/km)": round((lap.get("elapsed_time", 0) / 60) / ((lap.get("distance", 1)) / 1000), 2)
                            if lap.get("distance", 0) > 0 else None,
                        }
                        for i, lap in enumerate(laps_data)
                    ])
                    df_laps_display = df_laps.copy()
                    if "Allure (min/km)" in df_laps_display.columns:
                        df_laps_display["Allure (mm:ss/km)"] = df_laps_display["Allure (min/km)"].apply(minutes_to_mmss)
                        df_laps_display.drop(columns=["Allure (min/km)"], inplace=True)
                    st.subheader("📋 Détail des splits")
                    st.dataframe(df_laps_display)
                else:
                    st.warning("Impossible de récupérer les laps.")

                # --- Streams depuis le cache
                cached = df_cache[df_cache["id"] == act_id]
                if not cached.empty:
                    distance_stream = [d / 1000 for d in cached.iloc[0]["Distance Stream"]]
                    fc_stream = cached.iloc[0]["FC Stream"]
                    velocity_stream = cached.iloc[0].get("Vitesse Stream", [])

                    if (
                        fc_stream is not None
                        and distance_stream is not None
                        and len(fc_stream) > 0
                        and len(distance_stream) > 0
                        and len(distance_stream) == len(fc_stream)
                    ):
                        df_hr = pd.DataFrame({
                            "Distance (km)": distance_stream,
                            "Fréquence cardiaque (bpm)": fc_stream,
                        })
                        hr_chart = (
                            alt.Chart(df_hr)
                            .mark_line(color="crimson")
                            .encode(
                                x=alt.X("Distance (km)", title="Distance (km)", scale=alt.Scale(zero=False)),
                                y=alt.Y("Fréquence cardiaque (bpm)", title="FC (bpm)", scale=alt.Scale(zero=False)),
                                tooltip=["Distance (km)", "Fréquence cardiaque (bpm)"]
                            )
                            .interactive()
                            .properties(width=700, height=300, title="Évolution de la FC")
                        )
                        st.altair_chart(hr_chart)
                    else:
                        st.info("Pas de données de fréquence cardiaque.")

                    if (
                        velocity_stream is not None
                        and distance_stream is not None
                        and len(velocity_stream) > 0
                        and len(distance_stream) > 0
                        and len(distance_stream) == len(velocity_stream)
                    ):
                        pace_stream = [16.6667 / v if v else None for v in velocity_stream]
                        pace_seconds = [p * 60 if p is not None else None for p in pace_stream]
                        df_pace = pd.DataFrame({
                            "Distance (km)": distance_stream,
                            "Allure (s/km)": pace_seconds,
                            "Allure (mm:ss/km)": [minutes_to_mmss(p) if p is not None else "" for p in pace_stream],
                        })
                        pace_chart = (
                            alt.Chart(df_pace)
                            .mark_line(color="steelblue")
                            .encode(
                                x=alt.X("Distance (km)", title="Distance (km)", scale=alt.Scale(zero=False)),
                                y=alt.Y(
                                    "`Allure (s/km)`",
                                    type="quantitative",
                                    title="Allure (mm:ss/km)",
                                    scale=alt.Scale(zero=False, reverse=True),
                                    axis=alt.Axis(labelExpr="timeFormat(datum.value*1000, '%M:%S')"),
                                ),
                                tooltip=[
                                    alt.Tooltip("Distance (km):Q", title="Distance (km)"),
                                    alt.Tooltip(field="Allure (mm:ss/km)", type="nominal", title="Allure (mm:ss/km)"),
                                ]
                            )
                            .interactive()
                            .properties(width=700, height=300, title="Évolution de l'allure")
                        )
                        st.altair_chart(pace_chart)
                    else:
                        missing_fields = []
                        if distance_stream is None or len(distance_stream) == 0:
                            missing_fields.append("Distance Stream")
                        if velocity_stream is None or len(velocity_stream) == 0:
                            missing_fields.append("Vitesse Stream")
                        if missing_fields:
                            st.warning(
                                "Strava n'a pas renvoyé les streams nécessaires : "
                                + ", ".join(missing_fields)
                                + "."
                            )
                        elif len(distance_stream) != len(velocity_stream):
                            st.warning(
                                "Strava a renvoyé des streams de longueurs différentes "
                                "pour calculer l'allure."
                            )
                        else:
                            st.warning("Strava n'a pas renvoyé les streams nécessaires.")
                else:
                    st.info("Aucune donnée de stream en cache pour cette activité.")
        else:
            st.info("Aucune activité marquée comme 'tempo'.")

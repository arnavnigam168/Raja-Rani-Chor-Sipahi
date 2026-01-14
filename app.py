import streamlit as st
import pandas as pd
import random
from typing import List, Dict, Tuple, Optional
import json
import time
from supabase_client import supabase

# Page configuration
st.set_page_config(page_title="Raja-Rani-Chor-Sipahi-Mantri", layout="wide")


# ----- Game helpers reused for Supabase-backed logic -----


def assign_roles_5_players(player_names: List[str]) -> Dict[str, str]:
    """Randomly assign roles for 5-player mode"""
    roles = ['Raja', 'Rani', 'Mantri', 'Sipahi', 'Chor']
    shuffled_players = player_names.copy()
    random.shuffle(shuffled_players)
    return dict(zip(shuffled_players, roles))


def assign_roles_4_players(player_names: List[str]) -> Dict[str, str]:
    """Randomly assign roles for 4-player mode"""
    roles = ['Raja', 'Mantri', 'Sipahi', 'Chor']
    shuffled_players = player_names.copy()
    random.shuffle(shuffled_players)
    return dict(zip(shuffled_players, roles))

def _player_with_role(player_names: List[str], role_assignment: Dict[str, str], role: str) -> str:
    return [p for p in player_names if role_assignment[p] == role][0]


def _eligible_hidden_players(player_names: List[str], role_assignment: Dict[str, str]) -> List[str]:
    """Players among whom Mantri must guess (everyone except Raja and Mantri)."""
    return [p for p in player_names if role_assignment[p] not in ['Raja', 'Mantri']]


def _infer_sipahi_4p(player_names: List[str], role_assignment: Dict[str, str], guessed_chor: str) -> str:
    """4-player mode: Sipahi is the other hidden player once Chor is chosen."""
    eligible = _eligible_hidden_players(player_names, role_assignment)
    return [p for p in eligible if p != guessed_chor][0]


def calculate_points_5_players(player_names: List[str], role_assignment: Dict[str, str],
                               mantri_guesses: Tuple[str, str], raja_rani_guess: str) -> Dict[str, int]:
    """Calculate points for 5-player mode"""
    points = {}
    
    # Initialize all players with 0 points for this round
    for player in player_names:
        points[player] = 0
    
    # Get actual roles
    actual_chor = [p for p in player_names if role_assignment[p] == 'Chor'][0]
    actual_sipahi = [p for p in player_names if role_assignment[p] == 'Sipahi'][0]
    actual_rani = [p for p in player_names if role_assignment[p] == 'Rani'][0]
    mantri_name = [p for p in player_names if role_assignment[p] == 'Mantri'][0]
    raja_name = [p for p in player_names if role_assignment[p] == 'Raja'][0]
    
    guessed_chor, guessed_sipahi = mantri_guesses
    
    # Mantri's guess evaluation
    mantri_both_correct = (guessed_chor == actual_chor and guessed_sipahi == actual_sipahi)
    
    if mantri_both_correct:
        # Mantri keeps 500, Chor gets 0
        points[mantri_name] = 500
        points[actual_chor] = 0
    else:
        # Mantri gets 0, Chor gets 500
        points[mantri_name] = 0
        points[actual_chor] = 500
    
    # Sipahi always gets 250
    points[actual_sipahi] = 250
    
    # Raja's guess evaluation
    if raja_rani_guess == actual_rani:
        # Raja keeps 1000
        points[raja_name] = 1000
    else:
        # Raja loses 500 (gets 500 instead of 1000), Chor gets additional 500
        points[raja_name] = 500
        points[actual_chor] += 500
    
    # Rani always gets 750
    points[actual_rani] = 750
    
    return points


def calculate_points_4_players(player_names: List[str], role_assignment: Dict[str, str],
                               mantri_guesses: Tuple[str, str]) -> Dict[str, int]:
    """Calculate points for 4-player mode"""
    points = {}
    
    # Initialize all players with 0 points for this round
    for player in player_names:
        points[player] = 0
    
    # Get actual roles
    actual_chor = [p for p in player_names if role_assignment[p] == 'Chor'][0]
    actual_sipahi = [p for p in player_names if role_assignment[p] == 'Sipahi'][0]
    mantri_name = [p for p in player_names if role_assignment[p] == 'Mantri'][0]
    raja_name = [p for p in player_names if role_assignment[p] == 'Raja'][0]
    
    guessed_chor, guessed_sipahi = mantri_guesses
    
    # Mantri's guess evaluation
    mantri_correct = (guessed_chor == actual_chor and guessed_sipahi == actual_sipahi)
    
    if mantri_correct:
        # Mantri keeps 500
        points[mantri_name] = 500
        points[actual_chor] = 0
    else:
        # Mantri gets 0, Chor gets 500
        points[mantri_name] = 0
        points[actual_chor] = 500
    
    # Raja always keeps 1000
    points[raja_name] = 1000
    
    # Sipahi always keeps 250
    points[actual_sipahi] = 250
    
    return points


# ----- Supabase-backed multiplayer helpers -----

PHASE_RAJA_REVEAL = "RAJA_REVEAL"
PHASE_MANTRI_REVEAL = "MANTRI_REVEAL"
PHASE_MANTRI_GUESS = "MANTRI_GUESS"
PHASE_RAJA_GUESS = "RAJA_GUESS"
PHASE_ROUND_RESULT = "ROUND_RESULT"
PHASE_GAME_OVER = "GAME_OVER"


def init_local_state() -> None:
    """Local-only UI/session state (Supabase holds all game state)."""
    if "view" not in st.session_state:
        st.session_state.view = "home"  # home | host_setup | join_room | room
    if "room_code" not in st.session_state:
        st.session_state.room_code = None
    if "is_admin" not in st.session_state:
        st.session_state.is_admin = False
    if "player_name" not in st.session_state:
        st.session_state.player_name = ""
    if "admin_name" not in st.session_state:
        st.session_state.admin_name = None
    if "admin_name_input" not in st.session_state:
        st.session_state.admin_name_input = ""
    if "last_room_snapshot" not in st.session_state:
        st.session_state.last_room_snapshot = None
    if "lobby_join_logged" not in st.session_state:
        st.session_state.lobby_join_logged = False


def room_signature(room: Dict) -> Dict:
    """Small snapshot of key room fields for change detection."""
    lobby_log = room.get("lobby_log") or []
    return {
        "current_round": room.get("current_round"),
        "current_phase": room.get("current_phase"),
        "mantri_chor_guess": room.get("mantri_chor_guess"),
        "mantri_sipahi_guess": room.get("mantri_sipahi_guess"),
        "raja_rani_guess": room.get("raja_rani_guess"),
        "lobby_log_len": len(lobby_log) if isinstance(lobby_log, list) else 0,
    }


def poll_for_changes_if_waiting(room: Dict, is_waiting: bool) -> None:
    """
    Poll Supabase for room changes when this client is *waiting*.

    IMPORTANT:
    Streamlit will not update "other players" unless their script re-runs.
    So, when waiting, we intentionally do a low-frequency rerun loop (~1.5s)
    that keeps the UI synced without requiring manual refresh.

    - Stores last snapshot in st.session_state.last_room_snapshot
    - Never call inside forms or for the active acting player (we only call it
      in the "waiting for X" branches).
    """
    sig = room_signature(room)

    # If we're not in a waiting state, just keep snapshot in sync and return.
    if not is_waiting:
        st.session_state.last_room_snapshot = sig
        return

    # Update snapshot (helps with debugging / future optimizations)
    st.session_state.last_room_snapshot = sig

    # Keep waiting clients in sync: periodic rerun loop.
    # This does NOT kick users out because session_state is preserved across reruns.
    time.sleep(1.5)
    st.rerun()

def random_room_code(length: int = 6) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(length))


def fetch_room_by_code(code: str) -> Optional[Dict]:
    code = code.strip().upper()
    if not code:
        return None
    query = supabase.table("rooms").select("*").eq("room_code", code)
    res = query.execute()
    data = res.data
    return data[0] if data else None


def fetch_room(room_code: str) -> Optional[Dict]:
    query = supabase.table("rooms").select("*").eq("room_code", room_code)
    res = query.execute()
    data = res.data
    return data[0] if data else None


def fetch_players(room_code: str) -> List[Dict]:
    # order by player_name for deterministic seating order (seat_index not in schema)
    query = supabase.table("players").select("*").eq("room_code", room_code).order("player_name")
    res = query.execute()
    data = res.data
    return data or []


def fetch_round_scores(room_code: str) -> List[Dict]:
    res = (
        supabase.table("round_scores").select("*").eq("room_code", room_code).order("round_number")
    )
    res = res.execute()
    data = res.data
    return data or []



def create_room(room_data: Dict) -> Dict:
    """Insert a new room row. `room_data` must include `admin_name` (non-empty).
    Returns the created row dict or {} on failure.
    """
    admin = room_data.get("admin_name")
    if not admin:
        raise AssertionError("admin_name must be provided and non-empty when creating a room")

    # Use insert() for creation (do not upsert/insert partial objects elsewhere)
    res = supabase.table("rooms").insert(room_data).execute()
    data = res.data
    if isinstance(data, list):
        return data[0] if data else {}
    return data or {}


def update_room(room_code: str, updates: Dict) -> Dict:
    """Update an existing room by `room_code` with the given `updates` dict.
    This will never modify `admin_name`.
    Returns the updated row dict (if returned by Supabase) or {}.
    """
    if not room_code:
        raise AssertionError("room_code required for update")
    # Ensure admin_name is not being overwritten
    if "admin_name" in updates:
        updates = {k: v for k, v in updates.items() if k != "admin_name"}

    res = supabase.table("rooms").update(updates).eq("room_code", room_code).execute()
    data = res.data
    if isinstance(data, list):
        return data[0] if data else {}
    return data or {}


def insert_players(players: List[Dict]) -> List[Dict]:
    if not players:
        return []
    # Avoid chaining .select() after insert()
    res = supabase.table("players").insert(players).execute()
    data = res.data
    if not data:
        return []
    # Ensure a list is returned
    return data if isinstance(data, list) else [data]


def insert_round_scores(rows: List[Dict]) -> None:
    if not rows:
        return
    supabase.table("round_scores").insert(rows).execute()


def compute_roles_for_round(player_ids: List[str], num_players: int) -> Dict[str, str]:
    """Assign roles randomly for a list of player IDs."""
    # Expect a list of player names and return {player_name: role}
    if num_players == 5:
        return assign_roles_5_players(player_ids)
    return assign_roles_4_players(player_ids)


def build_scoreboard_from_db(room_code: str, players: List[Dict], num_rounds: int) -> pd.DataFrame:
    """Player | Round 1 | ... | Total, based on round_scores table."""
    scores = fetch_round_scores(room_code)
    # map[(player_name, round)] -> points
    points_map: Dict[Tuple[str, int], int] = {}
    for row in scores:
        pname = row["player_name"]
        rnd = int(row["round_number"])
        pts = int(row.get("points", 0))
        points_map[(pname, rnd)] = points_map.get((pname, rnd), 0) + pts

    cols = [f"Round {i}" for i in range(1, num_rounds + 1)]
    data_rows = []
    for p in players:
        pname = p.get("player_name")
        row = {"Player": pname}
        total = 0
        for rnd, col in enumerate(cols, start=1):
            v = points_map.get((pname, rnd), 0)
            row[col] = v
            total += v
        row["Total"] = total
        data_rows.append(row)

    return pd.DataFrame(data_rows)


def append_lobby_event(room_code: str, text: str) -> None:
    """Append a lobby event to rooms.lobby_log (JSON list)."""
    try:
        room = fetch_room(room_code)
        if not room:
            return
        log = room.get("lobby_log") or []
        if not isinstance(log, list):
            log = []
        log.append(text)
        update_room(room_code, {"lobby_log": log})
    except Exception:
        # Lobby messages are non-critical; never crash the game.
        pass


def main():
    init_local_state()
    st.title("🎴 Raja-Rani-Chor-Sipahi-Mantri (Online)")
    st.markdown("---")

    # Lightweight polling to keep clients in sync
    if st.session_state.room_code:
        pass

    # Not in a room yet: choose Host or Join
    if st.session_state.room_code is None and st.session_state.view == "home":
        col1, col2 = st.columns(2)
        with col1:
            if st.button("👑 Host a Game", use_container_width=True):
                st.session_state.view = "host_setup"
                st.session_state.is_admin = True
                st.rerun()
        with col2:
            if st.button("🙋 Join a Game", use_container_width=True):
                st.session_state.view = "join_room"
                st.session_state.is_admin = False
                st.rerun()
        return

    # Host creates room, chooses players & rounds
    if st.session_state.room_code is None and st.session_state.view == "host_setup":
        st.header("Create Room (Admin)")
        num_players = st.radio("Number of players", [4, 5], index=0, horizontal=True)
        num_rounds = st.slider("Number of rounds", min_value=1, max_value=20, value=5)
        st.write("Enter player names in seating order:")

        names: List[str] = []
        for i in range(num_players):
            val = st.text_input(f"Player {i+1} name", key=f"host_name_{i}")
            if val:
                names.append(val.strip())

        cleaned = [n for n in names if n]
        all_filled = len(cleaned) == num_players and len(set(cleaned)) == num_players
        if len(cleaned) == num_players and len(set(cleaned)) != num_players:
            st.error("Player names must be unique.")

        # Admin name input persists across reruns via session_state
        st.text_input("Which player are you? (type exact name)", key="admin_name_input")

        if all_filled and st.button("Create Room", use_container_width=True):
            # Read admin name exclusively from session_state to avoid Streamlit rerun issues
            admin_input = st.session_state.get("admin_name_input", "").strip()
            if not admin_input:
                st.error("Please enter which player you are (choose a name from the list).")
                st.stop()
            if admin_input not in cleaned:
                st.error("Admin name must match one of the entered player names.")
                st.stop()

            # Persist authoritative admin name
            st.session_state.admin_name = admin_input

            code = random_room_code()
            # Create room in Supabase (include admin_name to satisfy NOT NULL)
            room_insert = {
                "room_code": code,
                "num_players": num_players,
                "num_rounds": num_rounds,
                "current_round": 1,
                "current_phase": None,
                "admin_name": admin_input,
            }
            # Create the room (insert only once)
            try:
                room = create_room(room_insert)
            except AssertionError as e:
                st.error(str(e))
                return


            # Create players (associate by room_code) — use canonical schema
            player_rows = []
            for idx, name in enumerate(cleaned):
                player_rows.append(
                    {
                        "room_code": code,
                        "player_name": name,
                        "role": None,
                        "total_score": 0,
                    }
                )
            created_players = insert_players(player_rows)
            admin_player = next((p for p in created_players if p.get("player_name") == admin_input), None)
            if not admin_player:
                st.error("Failed to create admin player row.")
                return

            # Mark admin & local session (use player_name as canonical identifier)
            st.session_state.player_name = admin_player.get("player_name")
            st.session_state.room_code = code
            st.session_state.is_admin = True
            if not st.session_state.lobby_join_logged:
                append_lobby_event(code, f"{admin_player.get('player_name')} joined the lobby")
                st.session_state.lobby_join_logged = True

            st.session_state.view = "room"
            st.success(f"Room created! Share code: **{code}**")
            st.rerun()
        return

    # Join existing room
    if st.session_state.room_code is None and st.session_state.view == "join_room":
        st.header("Join Room")
        code = st.text_input("Room Code").upper()
        name = st.text_input("Your name")

        if st.button("Join", use_container_width=True):
            room = fetch_room_by_code(code)
            if not room:
                st.error("Room not found.")
                return
            room_code = room["room_code"]
            players = fetch_players(room_code)

            # Try to attach to existing name or create if space
            existing = [p for p in players if p["player_name"] == name.strip()]
            if existing:
                player = existing[0]
            else:
                if len(players) >= int(room.get("num_players", 0)):
                    st.error("Room is full.")
                    return
                insert = {
                    "room_code": room_code,
                    "player_name": name.strip(),
                    "role": None,
                    "total_score": 0,
                }
                created = insert_players([insert])
                player = created[0] if created else None
                if not player:
                    st.error("Failed to create player row.")
                    return

            st.session_state.room_code = room["room_code"]
            st.session_state.player_name = player["player_name"]
            # Determine admin status dynamically from room.admin_name
            st.session_state.is_admin = (st.session_state.player_name == room.get("admin_name"))
            if not st.session_state.lobby_join_logged:
                append_lobby_event(room_code, f"{player['player_name']} joined the lobby")
                st.session_state.lobby_join_logged = True
            st.session_state.view = "room"
            st.rerun()
        return

    # In-room experience (both admin and regular players)
    if st.session_state.room_code:
        room = fetch_room(st.session_state.room_code)
        if not room:
            st.error("Room not found or deleted.")
            return

        players = fetch_players(room["room_code"])
        if not players:
            st.error("No players found in this room.")
            return

        num_players = int(room.get("num_players", len(players)))
        num_rounds = int(room.get("num_rounds", 1))
        current_round = int(room.get("current_round", 1))
        phase = room.get("current_phase")
        roles_map = room.get("current_roles") or {}  # expects {player_name: role}

        # Header & room info
        st.subheader(f"Room: {room.get('room_code', '')}")
        st.write(
            f"Player: **{st.session_state.player_name}**  "
            f"| Round: **{current_round}/{num_rounds}**  "
            f"| Phase: **{phase or 'LOBBY'}**"
        )

        # Live scoreboard
        st.markdown("#### 📊 Scoreboard")
        scoreboard_df = build_scoreboard_from_db(room["room_code"], players, num_rounds)
        st.dataframe(scoreboard_df, use_container_width=True, hide_index=True)

        # Lobby activity log visible to all players
        lobby_log = room.get("lobby_log") or []
        with st.expander("Lobby activity", expanded=(phase is None)):
            if isinstance(lobby_log, list) and lobby_log:
                for entry in lobby_log:
                    st.write(str(entry))
            else:
                st.write("No lobby events yet.")

        this_player = next((p for p in players if p.get("player_name") == st.session_state.player_name), None)
        if not this_player:
            st.error("Your player row was not found in this room.")
            return
        this_player_name = this_player.get("player_name")
        # Everyone can always see their own role (once roles assigned)
        if roles_map and this_player_name in roles_map:
            st.info(f"Your role this round: **{roles_map[this_player_name]}**")

        # Admin-only controls to start first round
        if phase is None:
            st.warning("Waiting for admin to start the game.")
            if st.session_state.is_admin:
                st.write(f"Players registered: {len(players)}")
                if len(players) == num_players:
                    if st.button("Start Game (Assign Roles)", use_container_width=True):
                        player_names = [p.get("player_name") for p in players]
                        roles = compute_roles_for_round(player_names, num_players)
                        updates = {
                            "current_round": 1,
                            "current_phase": PHASE_RAJA_REVEAL,
                            "current_roles": roles,
                            "mantri_chor_guess": None,
                            "mantri_sipahi_guess": None,
                            "raja_rani_guess": None,
                        }
                        update_room(room["room_code"], updates)
                        st.rerun()
            else:
                # Non-admins in lobby wait for admin / new joins → poll
                poll_for_changes_if_waiting(room, is_waiting=True)
            return

        # Helper: who is Raja/Mantri/etc in current round
        def player_with_role(role: str) -> Optional[Dict]:
            for p in players:
                if roles_map.get(p["player_name"]) == role:
                    return p
            return None

        raja = player_with_role("Raja")
        mantri = player_with_role("Mantri")
        chor = player_with_role("Chor")
        sipahi = player_with_role("Sipahi")
        rani = player_with_role("Rani") if num_players == 5 else None

        # ---- Phase: RAJA_REVEAL ----
        if phase == PHASE_RAJA_REVEAL:
            st.markdown("### Phase: Raja Reveal")
            if raja:
                st.info(f"👑 Raja is: **{raja['player_name']}**")
            if st.session_state.is_admin:
                if st.button("Continue → Mantri Reveal", use_container_width=True):
                    update_room(room["room_code"], {"current_phase": PHASE_MANTRI_REVEAL})
                    st.rerun()
            else:
                # Non-admins wait for admin to advance
                poll_for_changes_if_waiting(room, is_waiting=True)
            return

        # ---- Phase: MANTRI_REVEAL ----
        if phase == PHASE_MANTRI_REVEAL:
            st.markdown("### Phase: Mantri Reveal")
            if mantri:
                st.info(f"🧠 Mantri is: **{mantri['player_name']}**")
            if st.session_state.is_admin:
                if st.button("Continue → Mantri Guess", use_container_width=True):
                    update_room(room["room_code"], {"current_phase": PHASE_MANTRI_GUESS})
                    st.rerun()
            else:
                # Non-admins wait for admin to advance
                poll_for_changes_if_waiting(room, is_waiting=True)
            return

        # ---- Phase: MANTRI_GUESS ----
        if phase == PHASE_MANTRI_GUESS:
            st.markdown("### Phase: Mantri Guess")
            if not mantri or this_player_name != mantri["player_name"]:
                st.info(f"Waiting for **{mantri['player_name']}** to guess..." if mantri else "Waiting for Mantri...")
                # Everyone except Mantri is waiting
                poll_for_changes_if_waiting(room, is_waiting=True)
                return

            # Only Mantri sees guess UI
            hidden_names = [
                p["player_name"]
                for p in players
                if roles_map.get(p["player_name"]) not in ["Raja", "Mantri"]
            ]

            with st.form("mantri_guess_form"):
                chor_choice_name = st.selectbox(
                    "Select Chor",
                    options=hidden_names,
                )
                if num_players == 5:
                    sipahi_options = [n for n in hidden_names if n != chor_choice_name]
                    sipahi_choice_name = st.selectbox(
                        "Select Sipahi",
                        options=sipahi_options,
                    )
                else:
                    # In 4-player mode, Sipahi inferred
                    sipahi_choice_name = next((n for n in hidden_names if n != chor_choice_name), None)
                    if sipahi_choice_name is None:
                        st.error("Unable to determine Sipahi choice.")
                        return

                submitted = st.form_submit_button("Submit Guess", use_container_width=True)

            if submitted:
                update = {
                    "mantri_chor_guess": chor_choice_name,
                    "mantri_sipahi_guess": sipahi_choice_name,
                }
                # For 4-player, we can fully resolve now
                if num_players == 4:
                    player_names = [p["player_name"] for p in players]
                    pts = calculate_points_4_players(player_names, roles_map, (chor_choice_name, sipahi_choice_name))
                    rows = []
                    for p in players:
                        pname = p["player_name"]
                        rows.append(
                            {
                                "room_code": room["room_code"],
                                "player_name": pname,
                                "round_number": current_round,
                                "points": int(pts.get(pname, 0)),
                            }
                        )
                    insert_round_scores(rows)
                    update["current_phase"] = PHASE_ROUND_RESULT
                else:
                    update["current_phase"] = PHASE_RAJA_GUESS

                update_room(room["room_code"], update)
                st.rerun()
            return

        # ---- Phase: RAJA_GUESS (5-player only) ----
        if phase == PHASE_RAJA_GUESS:
            st.markdown("### Phase: Raja Guess (Rani)")
            if not raja:
                st.error("Raja not found for this round.")
                return

            if this_player_name != raja["player_name"]:
                st.info(f"Waiting for **{raja['player_name']}** to guess Rani...")
                # Everyone except Raja is waiting
                poll_for_changes_if_waiting(room, is_waiting=True)
                return

            # Raja UI
            candidate_names_list = [p["player_name"] for p in players if p["player_name"] != raja["player_name"]]
            with st.form("raja_guess_form"):
                rani_choice_name = st.selectbox(
                    "Select Rani",
                    options=candidate_names_list,
                )
                submitted = st.form_submit_button("Submit Raja Guess", use_container_width=True)

            if submitted:
                # Compute final points for this round
                player_names = [p["player_name"] for p in players]
                # Use scoring util with player_name identifiers
                pts = calculate_points_5_players(
                    player_names,
                    roles_map,
                    (room.get("mantri_chor_guess"), room.get("mantri_sipahi_guess")),
                    rani_choice_name,
                )
                rows = []
                for p in players:
                    pname = p["player_name"]
                    rows.append(
                        {
                            "room_code": room["room_code"],
                            "player_name": pname,
                            "round_number": current_round,
                            "points": int(pts.get(pname, 0)),
                        }
                    )
                insert_round_scores(rows)

                update_room(room["room_code"], {"raja_rani_guess": rani_choice_name, "current_phase": PHASE_ROUND_RESULT})
                st.rerun()
            return

        # ---- Phase: ROUND_RESULT ----
        if phase == PHASE_ROUND_RESULT:
            st.markdown("### Phase: Round Result")
            if raja:
                st.write(f"👑 Raja: **{raja['player_name']}**")
            if num_players == 5 and rani:
                st.write(f"👸 Rani: **{rani['player_name']}**")
            if mantri:
                st.write(f"🧠 Mantri: **{mantri['player_name']}**")
            if sipahi:
                st.write(f"🛡️ Sipahi: **{sipahi['player_name']}**")
            if chor:
                st.write(f"🕵️ Chor: **{chor['player_name']}**")

            # Simple per-round summary from round_scores
            all_scores = fetch_round_scores(room["room_code"])
            this_round_scores = [r for r in all_scores if int(r["round_number"]) == current_round]
            rows = []
            for r in this_round_scores:
                pname = r.get("player_name")
                rows.append(
                    {
                        "Player": pname,
                        "Role": roles_map.get(pname),
                        "Points This Round": int(r.get("points", 0)),
                    }
                )
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            # Admin: next round or finish
            if st.session_state.is_admin:
                if current_round < num_rounds:
                    if st.button("Next Round", use_container_width=True):
                        player_names = [p["player_name"] for p in players]
                        roles = compute_roles_for_round(player_names, num_players)
                        update_room(
                            room["room_code"],
                            {
                                "current_round": current_round + 1,
                                "current_phase": PHASE_RAJA_REVEAL,
                                "current_roles": roles,
                                "mantri_chor_guess": None,
                                "mantri_sipahi_guess": None,
                                "raja_rani_guess": None,
                            },
                        )
                        st.rerun()
                else:
                    if st.button("Finish Game", use_container_width=True):
                        update_room(room["room_code"], {"current_phase": PHASE_GAME_OVER})
                        st.rerun()
            else:
                # Non-admins wait for admin to continue or finish
                poll_for_changes_if_waiting(room, is_waiting=True)
            return

        # ---- Phase: GAME_OVER ----
        if phase == PHASE_GAME_OVER:
            st.header("Game Complete!")
            st.subheader("📊 Final Scoreboard")
            scoreboard_df = build_scoreboard_from_db(room["room_code"], players, num_rounds)
            sorted_df = scoreboard_df.sort_values(by="Total", ascending=False, kind="mergesort").reset_index(drop=True)
            st.dataframe(sorted_df, use_container_width=True, hide_index=True)

            # Winner(s)
            if not sorted_df.empty:
                top_score = int(sorted_df.iloc[0]["Total"])
                winners = sorted_df[sorted_df["Total"] == top_score]["Player"].tolist()
                if len(winners) == 1:
                    st.success(f"🎉 Winner: **{winners[0]}** with {top_score} points!")
                else:
                    st.success(f"🎉 Joint Winners: **{', '.join(winners)}** with {top_score} points!")

            if st.button("Leave Room", use_container_width=True):
                # Local-only reset
                st.session_state.view = "home"
                st.session_state.room_code = None
                st.session_state.player_name = ""
                st.session_state.is_admin = False
                st.rerun()
            return


if __name__ == "__main__":
    main()

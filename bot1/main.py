import helper as unswbc
from helper import Direction, EdgeType
import numpy as np
import random

# --- ENGINE GLOBALS ---
ct: unswbc.Controller
game: unswbc.Game

# Seed generator for reproducible local matches
random.seed(0)

# --- CONFIG & SONAR ENCRYPTION ---
AUTH_KEY = 0xA5B7

def pack_sonar(msg_type: int, target_x: int, target_y: int, sender_id: int) -> int:
    """Packs role, coordinates, and sender ID into a 64-bit int with an XOR checksum."""
    payload = ((msg_type & 0xF) << 44) | ((target_x & 0xFF) << 36) | ((target_y & 0xFF) << 28) | (sender_id & 0x0FFFFFFF)
    checksum = AUTH_KEY ^ (payload & 0xFFFF) ^ ((payload >> 16) & 0xFFFF) ^ ((payload >> 32) & 0xFFFF)
    return ((checksum & 0xFFFF) << 48) | payload

def unpack_sonar(raw_val: int):
    """Decodes 64-bit sonar. Returns None if authentication fails (enemy ping)."""
    checksum = (raw_val >> 48) & 0xFFFF
    payload = raw_val & 0x0000FFFFFFFFFFFF
    expected_check = AUTH_KEY ^ (payload & 0xFFFF) ^ ((payload >> 16) & 0xFFFF) ^ ((payload >> 32) & 0xFFFF)
    
    if checksum != expected_check:
        return None  # Intercepted enemy message or invalid payload
        
    msg_type = (payload >> 44) & 0x0F
    target_x = (payload >> 36) & 0xFF
    target_y = (payload >> 28) & 0xFF
    sender_id = payload & 0x0FFFFFFF
    return msg_type, target_x, target_y, sender_id

# --- VISION & HAZARD MAPPING ---
def get_hazard_matrix(controller: unswbc.Controller) -> np.ndarray:
    """Creates a 7x7 boolean matrix: True = Body/Hazard, False = Safe."""
    hazard_map = np.zeros((7, 7), dtype=bool)
    head_pos = controller.get_position()
    my_id = controller.get_id()
    my_team = controller.get_team()
    
    for tile in controller.get_tiles():
        tpos = tile.get_position()
        rx = tpos.x - head_pos.x + 3
        ry = tpos.y - head_pos.y + 3
        
        if 0 <= rx < 7 and 0 <= ry < 7:
            part = tile.get_dragon()
            if part is not None:
                # Enemy head with lower ID moves first -> mark 1-tile radius as hazard
                if part.is_head() and part.get_team() != my_team and part.get_id() < my_id:
                    hazard_map[max(0, ry-1):min(7, ry+2), max(0, rx-1):min(7, rx+2)] = True
                else:
                    hazard_map[ry, rx] = True
    return hazard_map

# --- MOVEMENT LOGIC ---
def safe_step(hazard_map: np.ndarray, target_pos=None) -> None:
    """Finds an open, passable direction and executes the move."""
    here = ct.get_position()
    here_tile = ct.get_tile(here)
    
    directions = Direction.get_direction_list()
    random.shuffle(directions)
    
    best_dir = None

    for direction in directions:
        # 1. Check wall/kelp edge
        if here_tile.get_edge(direction).get_edge_type() == EdgeType.KELP:
            continue

        # 2. Check tile ahead
        ahead_pos = here.add_dir(direction)
        ahead_tile = ct.get_tile(ahead_pos)
        
        if ahead_tile is None or ahead_tile.get_dragon() is not None:
            continue

        # 3. Check 7x7 hazard matrix
        rx = ahead_pos.x - here.x + 3
        ry = ahead_pos.y - here.y + 3
        if 0 <= rx < 7 and 0 <= ry < 7 and hazard_map[ry, rx]:
            continue

        # Priority A: Step directly on pearls
        if ahead_tile.has_pearl() and target_pos is None:
            ct.make_move(direction)
            return

        # Priority B: Move toward target coordinate
        if target_pos is not None:
            if abs(ahead_pos.x - target_pos.x) < abs(here.x - target_pos.x) or \
               abs(ahead_pos.y - target_pos.y) < abs(here.y - target_pos.y):
                best_dir = direction
                break

        best_dir = direction

    if best_dir is not None:
        ct.make_move(best_dir)
    else:
        # Fallback: step into any valid non-kelp direction
        for direction in directions:
            if here_tile.get_edge(direction).get_edge_type() != EdgeType.KELP:
                ct.make_move(direction)
                return

# --- MAIN TURN LOGIC ---
def execute_turn() -> None:
    """Evaluates turn state for the active dragon."""
    try:
        my_id = ct.get_id()
        my_length = ct.get_length()
        here = ct.get_position()
        hazard_map = get_hazard_matrix(ct)

        # 1. QUEEN ROLE (ID 0 or explicitly tagged)
        if my_id == 0 or ct.get_indicator_string().startswith("QUEEN"):
            ct.set_indicator_string(f"QUEEN [L:{my_length}]")
            
            # Send backwards sonar through tail
            tail_dir = ct.get_dir().get_opposite()
            encoded_msg = pack_sonar(0, here.x, here.y, my_id)
            ct.send_sonar(tail_dir, encoded_msg)
            
            safe_step(hazard_map)
            return

        # 2. WORKER / HARVESTER ROLE
        queen_pos = None
        for msg in ct.get_sonar_messages():
            data = unpack_sonar(msg)
            if data and data[0] == 0:  # Msg Type 0 = Queen Position
                queen_pos = ct.get_tile(here).get_position() # Target position reference

        # Aggressive Splitting
        if my_length >= 6 and ct.can_split(3):
            ct.do_split(3)
            ct.set_indicator_string("SCOUT")
            return

        # Cannibal Funnel Mode
        if my_length >= 8 and queen_pos is not None:
            ct.set_indicator_string("FUNNEL")
            safe_step(hazard_map, target_pos=queen_pos)
            return

        # Default Harvesting
        ct.set_indicator_string("HARVESTER")
        safe_step(hazard_map)

    except Exception as e:
        # Emergency Fallback: Ensures dragons never freeze if an edge-case exception occurs
        safe_step(np.zeros((7, 7), dtype=bool))

# --- BATTLECODE ENGINE LOOP ---
def main() -> None:
    global ct, game
    ct, game = unswbc.init()

    while unswbc.update(ct, game):
        execute_turn()
        unswbc.end_turn()

if __name__ == "__main__":
    main()
import helper as unswbc
from helper import Direction, EdgeType
import collections
import random
import traceback

# =============================================================================
# 1. COMPACT RESILIENT SONAR PROTOCOL
# =============================================================================
class SonarProtocol:
    XOR_MASK = 0x5A3C96E1B2D4F8A0  # Obfuscation mask
    MAGIC_KEY = 0xA5               # Protocol signature byte
    
    # Message Types
    MSG_CLAIM_PEARL = 0x1
    MSG_THREAT_ALERT = 0x2

    @staticmethod
    def encode(msg_type: int, target_x: int, target_y: int, sender_id: int) -> int:
        """
        64-bit Bitfield Layout:
        [63..56] 8b : MAGIC_KEY (0xA5)
        [55..52] 4b : MSG_TYPE
        [51..46] 6b : TARGET_X (0..63)
        [45..40] 6b : TARGET_Y (0..63)
        [39..24] 16b: SENDER_ID
        [23..0]  24b: Padding / Unused
        """
        raw = (SonarProtocol.MAGIC_KEY & 0xFF) << 56
        raw |= (msg_type & 0xF) << 52
        raw |= (target_x & 0x3F) << 46
        raw |= (target_y & 0x3F) << 40
        raw |= (sender_id & 0xFFFF) << 24
        return raw ^ SonarProtocol.XOR_MASK

    @staticmethod
    def decode(val: int):
        raw = val ^ SonarProtocol.XOR_MASK
        magic = (raw >> 56) & 0xFF
        if magic != SonarProtocol.MAGIC_KEY:
            return None  # Invalid message or enemy noise
            
        msg_type = (raw >> 52) & 0xF
        target_x = (raw >> 46) & 0x3F
        target_y = (raw >> 40) & 0x3F
        sender_id = (raw >> 24) & 0xFFFF
        return msg_type, target_x, target_y, sender_id


# =============================================================================
# 2. INTERNAL MAP & GEOMETRY ENGINE
# =============================================================================
class MapTracker:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.pearls = set()
        self.edges = {}       # ((x,y), dir_val) -> EdgeType
        self.portals = {}     # portal_id -> set of (x,y)
        
    def normalize(self, x: int, y: int):
        return (x % self.width, y % self.height)
        
    def step_dir(self, x: int, y: int, direction: Direction):
        if direction == Direction.NORTH: return self.normalize(x, y - 1)
        if direction == Direction.SOUTH: return self.normalize(x, y + 1)
        if direction == Direction.EAST:  return self.normalize(x + 1, y)
        if direction == Direction.WEST:  return self.normalize(x - 1, y)
        return (x, y)

    def update_vision(self, ct: unswbc.Controller):
        for tile in ct.get_tiles():
            if tile is None:
                continue
            pos = tile.get_position()
            x, y = pos.x, pos.y
            
            # Pearl State
            if tile.has_pearl():
                self.pearls.add((x, y))
            else:
                self.pearls.discard((x, y))
                
            # Topology & Portals
            for d in Direction.get_direction_list():
                edge = tile.get_edge(d)
                etype = edge.get_edge_type()
                self.edges[((x, y), d.value)] = etype
                
                if etype == EdgeType.PORTAL:
                    pid = edge.get_portal_id() if hasattr(edge, 'get_portal_id') else 0
                    if pid not in self.portals:
                        self.portals[pid] = set()
                    self.portals[pid].add((x, y))

    def get_portal_exit(self, x: int, y: int, direction: Direction):
        if self.edges.get(((x, y), direction.value), EdgeType.EMPTY) != EdgeType.PORTAL:
            return None
        for pid, coords in self.portals.items():
            if (x, y) in coords:
                for exit_coord in coords:
                    if exit_coord != (x, y):
                        return exit_coord
        return None


# =============================================================================
# 3. SAFETY & TACTICAL EVALUATOR
# =============================================================================
class SafetyEvaluator:
    @staticmethod
    def flood_fill_volume(start_x: int, start_y: int, map_tracker: MapTracker, 
                           occupied_tiles: set, max_depth: int) -> int:
        """Calculates reachable tile volume using BFS up to max_depth."""
        queue = collections.deque([(start_x, start_y)])
        visited = set([(start_x, start_y)])
        volume = 0
        
        while queue and volume < max_depth:
            cx, cy = queue.popleft()
            volume += 1
            
            for d in Direction.get_direction_list():
                # Kelp check
                if map_tracker.edges.get(((cx, cy), d.value), EdgeType.EMPTY) == EdgeType.KELP:
                    continue
                nx, ny = map_tracker.step_dir(cx, cy, d)
                if (nx, ny) not in visited and (nx, ny) not in occupied_tiles:
                    visited.add((nx, ny))
                    queue.append((nx, ny))
        return volume

    @staticmethod
    def is_step_safe(start_x: int, start_y: int, direction: Direction, 
                     map_tracker: MapTracker, occupied_tiles: set, 
                     enemy_threat_zones: set, my_len: int) -> bool:
        """Evaluates strict zero-suicide movement rules."""
        edge_type = map_tracker.edges.get(((start_x, start_y), direction.value), EdgeType.EMPTY)
        
        # 1. Kelp barrier
        if edge_type == EdgeType.KELP:
            return False
            
        # 2. Portal destination safety
        if edge_type == EdgeType.PORTAL:
            portal_exit = map_tracker.get_portal_exit(start_x, start_y, direction)
            if portal_exit is None:
                return False  # Portal exit unmapped
            ex, ey = portal_exit
            if (ex, ey) in occupied_tiles or (ex, ey) in enemy_threat_zones:
                return False
            # Check exit side reachable volume
            exit_vol = SafetyEvaluator.flood_fill_volume(ex, ey, map_tracker, occupied_tiles, my_len + 2)
            return exit_vol >= my_len + 2

        # 3. Standard step safety
        nx, ny = map_tracker.step_dir(start_x, start_y, direction)
        if (nx, ny) in occupied_tiles:
            return False
        if (nx, ny) in enemy_threat_zones:
            return False
            
        return True


# =============================================================================
# 4. PATHFINDING ENGINE
# =============================================================================
def find_bfs_path(start_x: int, start_y: int, target_x: int, target_y: int, 
                  map_tracker: MapTracker, occupied_tiles: set, enemy_threat_zones: set):
    if start_x == target_x and start_y == target_y:
        return None
        
    queue = collections.deque([(start_x, start_y, None)])
    visited = set([(start_x, start_y)])
    
    while queue:
        cx, cy, first_move = queue.popleft()
        if cx == target_x and cy == target_y:
            return first_move
            
        for d in Direction.get_direction_list():
            if map_tracker.edges.get(((cx, cy), d.value), EdgeType.EMPTY) == EdgeType.KELP:
                continue
            nx, ny = map_tracker.step_dir(cx, cy, d)
            if (nx, ny) in visited or (nx, ny) in occupied_tiles or (nx, ny) in enemy_threat_zones:
                continue
                
            visited.add((nx, ny))
            move = first_move if first_move is not None else d
            queue.append((nx, ny, move))
            
    return None


# =============================================================================
# 5. MAIN BOT STATE & EXECUTION
# =============================================================================
ct: unswbc.Controller
game: unswbc.Game
map_tracker: MapTracker
claimed_pearls = set()

def execute_turn() -> None:
    global claimed_pearls
    claimed_pearls.clear()
    
    pos = ct.get_position()
    my_x, my_y = pos.x, pos.y
    my_len = ct.get_length()
    my_id = ct.get_id()
    my_team = ct.get_team()
    
    map_tracker.update_vision(ct)
    
    # -------------------------------------------------------------------------
    # A. READ SONAR SIGNALS
    # -------------------------------------------------------------------------
    for raw_msg in ct.get_sonar_messages():
        decoded = SonarProtocol.decode(raw_msg)
        if decoded:
            msg_type, tx, ty, sender_id = decoded
            if msg_type == SonarProtocol.MSG_CLAIM_PEARL:
                claimed_pearls.add((tx, ty))

    # -------------------------------------------------------------------------
    # B. BUILD MASK OF OCCUPIED TILES & ENEMY THREAT ZONES
    # -------------------------------------------------------------------------
    occupied_tiles = set()
    enemy_threat_zones = set()
    
    for tile in ct.get_tiles():
        if tile is None:
            continue
        part = tile.get_dragon()
        tx, ty = tile.get_position().x, tile.get_position().y
        
        if part:
            occupied_tiles.add((tx, ty))
            # If enemy head is equal or larger, mark its move neighbors as lethal
            if part.get_team() != my_team and part.is_head():
                elen = part.get_length() if hasattr(part, 'get_length') else 3
                if elen >= my_len:
                    enemy_threat_zones.add((tx, ty))
                    for d in Direction.get_direction_list():
                        nx, ny = map_tracker.step_dir(tx, ty, d)
                        enemy_threat_zones.add((nx, ny))

    # -------------------------------------------------------------------------
    # C. AUTONOMOUS ROLE ECONOMICS (NO FRAGILE ELECTION)
    # -------------------------------------------------------------------------
    # Unit 0 is the Capital Leviathan (never splits). Units > 0 are Forager Drones.
    is_leviathan = (my_id == 0)
    
    if not is_leviathan and my_len >= 5 and ct.get_unit_count() < 12:
        if ct.can_split(2):
            ct.output_log("Forager multiplying!")
            ct.do_split(2)
            return

    # -------------------------------------------------------------------------
    # D. TARGET SELECTION (PEARL FARMING)
    # -------------------------------------------------------------------------
    target_pos = None
    best_dist = 999999
    
    for px, py in map_tracker.pearls:
        if (px, py) in claimed_pearls:
            continue
        dx = min(abs(px - my_x), map_tracker.width - abs(px - my_x))
        dy = min(abs(py - my_y), map_tracker.height - abs(py - my_y))
        dist = dx + dy
        if dist < best_dist:
            best_dist = dist
            target_pos = (px, py)

    # -------------------------------------------------------------------------
    # E. PATHFINDING & VORONOI SPACE VERIFICATION
    # -------------------------------------------------------------------------
    chosen_move = None
    
    if target_pos:
        tx, ty = target_pos
        move = find_bfs_path(my_x, my_y, tx, ty, map_tracker, occupied_tiles, enemy_threat_zones)
        if move and SafetyEvaluator.is_step_safe(my_x, my_y, move, map_tracker, occupied_tiles, enemy_threat_zones, my_len):
            nx, ny = map_tracker.step_dir(my_x, my_y, move)
            vol = SafetyEvaluator.flood_fill_volume(nx, ny, map_tracker, occupied_tiles, max_depth=my_len + 2)
            if vol >= my_len + 2:
                chosen_move = move
                # Broadcast claim pulse across cardinal directions
                claim_code = SonarProtocol.encode(SonarProtocol.MSG_CLAIM_PEARL, tx, ty, my_id)
                for d in Direction.get_direction_list():
                    ct.send_sonar(d, claim_code)

    # -------------------------------------------------------------------------
    # F. SAFE FALLBACK EVALUATION
    # -------------------------------------------------------------------------
    if not chosen_move:
        candidate_moves = []
        for d in Direction.get_direction_list():
            if SafetyEvaluator.is_step_safe(my_x, my_y, d, map_tracker, occupied_tiles, enemy_threat_zones, my_len):
                nx, ny = map_tracker.step_dir(my_x, my_y, d)
                vol = SafetyEvaluator.flood_fill_volume(nx, ny, map_tracker, occupied_tiles, max_depth=my_len + 2)
                candidate_moves.append((d, vol))
                
        if candidate_moves:
            # Select move yielding maximum reachable volume
            candidate_moves.sort(key=lambda x: x[1], reverse=True)
            if candidate_moves[0][1] >= my_len + 2:
                chosen_move = candidate_moves[0][0]

    # -------------------------------------------------------------------------
    # G. EMERGENCY TRAP RECOVERY & GUARANTEED STEP
    # -------------------------------------------------------------------------
    if not chosen_move:
        # Trapped! Non-leviathans shed tail volume to reduce space requirement
        if not is_leviathan and my_len >= 4:
            split_amount = my_len // 2
            if split_amount >= 2 and ct.can_split(split_amount):
                ct.output_log("Emergency trap split!")
                ct.do_split(split_amount)
                return

        # Desperation step: Any non-kelp direction
        for d in Direction.get_direction_list():
            if map_tracker.edges.get(((my_x, my_y), d.value), EdgeType.EMPTY) != EdgeType.KELP:
                chosen_move = d
                break

    # Final command execution
    if chosen_move:
        ct.make_move(chosen_move)
    else:
        ct.make_move(Direction.NORTH)


# =============================================================================
# 6. ENGINE ENTRY POINT
# =============================================================================
def main() -> None:
    global ct, game, map_tracker
    ct, game = unswbc.init()
    w, h = game.get_map_size()
    map_tracker = MapTracker(w, h)
    
    while unswbc.update(ct, game):
        try:
            execute_turn()
        except Exception:
            for line in traceback.format_exc().split('\n'):
                if line:
                    ct.output_log(f"CRASH_PREVENTED: {line}")
        unswbc.end_turn()

if __name__ == "__main__":
    main()
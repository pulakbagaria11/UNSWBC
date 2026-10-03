import helper as unswbc
from helper import Direction, EdgeType
import random
import collections

# =============================================================================
# OBFUSCATED SONAR PROTOCOL (WITH SEPARATORS & XOR MASKING)
# =============================================================================
class MessageType:
    PING = 0
    CLAIM_PEARL = 1
    ENEMY_SPOTTED = 2
    QUEEN_HEARTBEAT = 3

class SonarMessage:
    # Secret 64-bit XOR mask disrupts generic magic-key sniffers
    XOR_MASK = 0x3C9A5F12B7E481D0  
    SEP = 0b11  # 2-bit separator field
    
    def __init__(self, msg_type, sender_x, sender_y, target_x, target_y, 
                 queen_x, queen_y, queen_id, sender_id):
        self.msg_type = msg_type
        self.sender_x = sender_x
        self.sender_y = sender_y
        self.target_x = target_x
        self.target_y = target_y
        self.queen_x = queen_x
        self.queen_y = queen_y
        self.queen_id = queen_id
        self.sender_id = sender_id
        
    def encode(self) -> int:
        """
        Interleaved bitfield structure with 2-bit separators:
        [63..60] 4b: MSG_TYPE
        [59..58] 2b: SEP (11)
        [57..52] 6b: SENDER_X
        [51..46] 6b: SENDER_Y
        [45..44] 2b: SEP (11)
        [43..38] 6b: TARGET_X
        [37..32] 6b: TARGET_Y
        [31..30] 2b: SEP (11)
        [29..24] 6b: QUEEN_X
        [23..18] 6b: QUEEN_Y
        [17..12] 6b: QUEEN_ID
        [11..0] 12b: SENDER_ID
        """
        raw = (self.msg_type & 0xF) << 60
        raw |= (self.SEP & 0x3) << 58
        raw |= (self.sender_x & 0x3F) << 52
        raw |= (self.sender_y & 0x3F) << 46
        raw |= (self.SEP & 0x3) << 44
        raw |= (self.target_x & 0x3F) << 38
        raw |= (self.target_y & 0x3F) << 32
        raw |= (self.SEP & 0x3) << 30
        raw |= (self.queen_x & 0x3F) << 24
        raw |= (self.queen_y & 0x3F) << 18
        raw |= (self.queen_id & 0x3F) << 12
        raw |= (self.sender_id & 0xFFF)
        
        # Apply XOR obfuscation mask
        return raw ^ self.XOR_MASK

    @staticmethod
    def decode(val: int):
        # Unmask raw integer
        raw = val ^ SonarMessage.XOR_MASK
        
        # Verify embedded separators
        sep1 = (raw >> 58) & 0x3
        sep2 = (raw >> 44) & 0x3
        sep3 = (raw >> 30) & 0x3
        if sep1 != SonarMessage.SEP or sep2 != SonarMessage.SEP or sep3 != SonarMessage.SEP:
            return None  # Failed separator verification (enemy pulse or junk noise)
            
        msg_type = (raw >> 60) & 0xF
        sender_x = (raw >> 52) & 0x3F
        sender_y = (raw >> 46) & 0x3F
        target_x = (raw >> 38) & 0x3F
        target_y = (raw >> 32) & 0x3F
        queen_x  = (raw >> 24) & 0x3F
        queen_y  = (raw >> 18) & 0x3F
        queen_id = (raw >> 12) & 0x3F
        sender_id = raw & 0xFFF
        
        return SonarMessage(msg_type, sender_x, sender_y, target_x, target_y, 
                            queen_x, queen_y, queen_id, sender_id)

# =============================================================================
# GLOBAL MAP & PORTAL SAFETY ENGINE
# =============================================================================
class GlobalMap:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.pearl_countdowns = {} 
        self.has_pearl = set()
        self.edges = {}
        self.portals = {}  # portal_id -> set of (x,y) coordinates
        
    def normalize(self, x, y):
        return (x % self.width, y % self.height)
        
    def add_dir(self, x, y, direction):
        if direction == Direction.NORTH:
            return self.normalize(x, y - 1)
        elif direction == Direction.SOUTH:
            return self.normalize(x, y + 1)
        elif direction == Direction.EAST:
            return self.normalize(x + 1, y)
        elif direction == Direction.WEST:
            return self.normalize(x - 1, y)
        return (x, y)
        
    def tick_round(self):
        for pos in list(self.pearl_countdowns.keys()):
            if self.pearl_countdowns[pos] > 0:
                self.pearl_countdowns[pos] -= 1
                
    def update_from_vision(self, ct):
        for tile in ct.get_tiles():
            if tile is None:
                continue
            pos = tile.get_position()
            x, y = pos.x, pos.y
            
            if hasattr(tile, 'get_pearl_time'):
                p_time = tile.get_pearl_time()
                if p_time >= 0:
                    self.pearl_countdowns[(x, y)] = p_time
                
            if tile.has_pearl():
                self.has_pearl.add((x, y))
            else:
                self.has_pearl.discard((x, y))
                
            for d in Direction.get_direction_list():
                edge = tile.get_edge(d)
                etype = edge.get_edge_type()
                self.edges[((x, y), d.value)] = etype
                
                if etype == EdgeType.PORTAL:
                    pid = edge.get_portal_id() if hasattr(edge, 'get_portal_id') else 0
                    if pid not in self.portals:
                        self.portals[pid] = set()
                    self.portals[pid].add((x, y))

    def get_portal_destination(self, x, y, direction):
        """Finds the paired exit coordinate for a portal edge."""
        edge_type = self.edges.get(((x, y), direction.value), EdgeType.EMPTY)
        if edge_type != EdgeType.PORTAL:
            return None
            
        for pid, coords in self.portals.items():
            if (x, y) in coords:
                for exit_coord in coords:
                    if exit_coord != (x, y):
                        return exit_coord
        return None

    def get_neighbors(self, x, y):
        neighbors = []
        for d in Direction.get_direction_list():
            edge_type = self.edges.get(((x, y), d.value), EdgeType.EMPTY)
            if edge_type == EdgeType.KELP:
                continue
            nx, ny = self.add_dir(x, y, d)
            neighbors.append((nx, ny, d))
        return neighbors

    def find_path_bfs(self, start_x, start_y, target_x, target_y, occupied_tiles):
        if start_x == target_x and start_y == target_y:
            return None
            
        queue = collections.deque([(start_x, start_y, None)])
        visited = set([(start_x, start_y)])
        
        while queue:
            cx, cy, first_move = queue.popleft()
            if cx == target_x and cy == target_y:
                return first_move
                
            for nx, ny, d in self.get_neighbors(cx, cy):
                if (nx, ny) in visited or (nx, ny) in occupied_tiles:
                    continue
                visited.add((nx, ny))
                move = first_move if first_move is not None else d
                queue.append((nx, ny, move))
                
        return None

# =============================================================================
# BATTLECODE BOT CORE LOGIC
# =============================================================================
ct: unswbc.Controller
game: unswbc.Game
global_map: GlobalMap

queen_id = None
queen_pos = None
queen_last_seen = 0
current_turn = 0
claimed_pearls = set()

def flood_fill_space(start_x, start_y, occupied_tiles, max_depth):
    queue = collections.deque([(start_x, start_y)])
    visited = set([(start_x, start_y)])
    space = 0
    
    while queue and space < max_depth:
        cx, cy = queue.popleft()
        space += 1
        
        for d in Direction.get_direction_list():
            edge_type = global_map.edges.get(((cx, cy), d.value), EdgeType.EMPTY)
            if edge_type == EdgeType.KELP:
                continue
            nx, ny = global_map.add_dir(cx, cy, d)
            if (nx, ny) not in visited and (nx, ny) not in occupied_tiles:
                visited.add((nx, ny))
                queue.append((nx, ny))
    return space

def is_safe_step(start_x, start_y, direction, occupied_tiles, my_len):
    edge_type = global_map.edges.get(((start_x, start_y), direction.value), EdgeType.EMPTY)
    
    # 1. Kelp boundary check
    if edge_type == EdgeType.KELP:
        return False
        
    # 2. Portal destination inspection
    if edge_type == EdgeType.PORTAL:
        portal_exit = global_map.get_portal_destination(start_x, start_y, direction)
        if portal_exit is not None:
            ex, ey = portal_exit
            if (ex, ey) in occupied_tiles:
                return False
            # Verify reachable volume on the exit side of the portal
            exit_space = flood_fill_space(ex, ey, occupied_tiles, max_depth=my_len + 2)
            if exit_space < my_len:
                return False  # Portal destination is a dead-end trap!
            return True

    # 3. Standard tile step check
    nx, ny = global_map.add_dir(start_x, start_y, direction)
    if (nx, ny) in occupied_tiles:
        return False
        
    return True

def execute_turn() -> None:
    global queen_id, queen_pos, queen_last_seen, current_turn, claimed_pearls
    current_turn += 1
    claimed_pearls.clear()
    
    here = ct.get_position()
    my_x, my_y = here.x, here.y
    my_len = ct.get_length()
    my_id = ct.get_id()
    my_team = ct.get_team()
    
    global_map.tick_round()
    global_map.update_from_vision(ct)
    
    # -------------------------------------------------------------------------
    # 1. SONAR INGESTION & DYNAMIC QUEEN SUCCESSION
    # -------------------------------------------------------------------------
    for msg_val in ct.get_sonar_messages():
        msg = SonarMessage.decode(msg_val)
        if msg:
            if msg.msg_type == MessageType.QUEEN_HEARTBEAT:
                # Update Queen identity and position
                if queen_id is None or msg.queen_id <= queen_id:
                    queen_id = msg.queen_id
                    queen_pos = (msg.queen_x, msg.queen_y)
                    queen_last_seen = current_turn
            elif msg.msg_type == MessageType.CLAIM_PEARL:
                claimed_pearls.add((msg.target_x, msg.target_y))

    # Dynamic Expiry: Reset Queen if missing for > 3 turns
    if (current_turn - queen_last_seen) > 3:
        queen_id = None

    # Auto-promotion: Lowest active ID claims Queen status
    if queen_id is None or my_id < queen_id:
        queen_id = my_id
        queen_pos = (my_x, my_y)
        queen_last_seen = current_turn

    is_queen = (my_id == queen_id)
    if is_queen:
        queen_pos = (my_x, my_y)

    # -------------------------------------------------------------------------
    # 2. LOCAL VISION & ENEMY DETECTOR
    # -------------------------------------------------------------------------
    occupied_tiles = set()
    enemy_heads = []
    queen_head_nearby = None
    
    for tile in ct.get_tiles():
        if tile is None: 
            continue
        part = tile.get_dragon()
        tx, ty = tile.get_position().x, tile.get_position().y
        
        if part:
            occupied_tiles.add((tx, ty))
            if part.get_team() != my_team and part.is_head():
                enemy_heads.append((tx, ty))
            elif part.get_team() == my_team and part.is_head() and part.get_id() == queen_id and not is_queen:
                queen_head_nearby = (tx, ty)

    # -------------------------------------------------------------------------
    # 3. QUEEN INTERACTION ("BE FOOD" OR "LEAVE" WITH FAIL-SAFE FALLBACK)
    # -------------------------------------------------------------------------
    if not is_queen and queen_head_nearby:
        qx, qy = queen_head_nearby
        dist_to_queen = abs(qx - my_x) + abs(qy - my_y)
        
        # A. "Be Food": Step directly into Queen's path if adjacent
        if dist_to_queen == 1:
            for d in Direction.get_direction_list():
                nx, ny = global_map.add_dir(my_x, my_y, d)
                if (nx, ny) == (qx, qy):
                    ct.output_log("Serving as food for the Queen!")
                    ct.make_move(d)
                    return

        # B. "Leave": Move away from Queen to clear her vector
        escape_moves = []
        for d in Direction.get_direction_list():
            if is_safe_step(my_x, my_y, d, occupied_tiles, my_len):
                nx, ny = global_map.add_dir(my_x, my_y, d)
                if (abs(nx - qx) + abs(ny - qy)) > dist_to_queen:
                    escape_moves.append(d)
        if escape_moves:
            ct.output_log("Yielding path to the Queen!")
            ct.make_move(random.choice(escape_moves))
            return
            
        # Fallback: If no ideal escape step exists, proceed to standard pathfinding!

    # -------------------------------------------------------------------------
    # 4. SWARM SPLITTING ECONOMICS
    # -------------------------------------------------------------------------
    if not is_queen and my_len >= 5 and ct.get_unit_count() < 64:
        if ct.can_split(2):
            ct.output_log("Splitting new harvester!")
            ct.do_split(2)
            return

    # -------------------------------------------------------------------------
    # 5. TARGET SELECTION (PORTAL BLOCKING vs PEARL FARMING vs HUNTING)
    # -------------------------------------------------------------------------
    target_pos = None

    # Priority A: Block portals if enemy heads are active
    if global_map.portals and enemy_heads:
        all_portal_tiles = [pos for p_set in global_map.portals.values() for pos in p_set]
        if all_portal_tiles:
            target_pos = min(all_portal_tiles, key=lambda p: min(abs(p[0] - my_x), abs(p[1] - my_y)))

    # Priority B: Hunt enemies if small harvester
    elif not is_queen and my_len <= 4 and enemy_heads:
        target_pos = enemy_heads[0]

    # Priority C: Farm pearls
    else:
        best_dist = 9999
        for px, py in global_map.has_pearl:
            if (px, py) in claimed_pearls:
                continue
            dx = min(abs(px - my_x), global_map.width - abs(px - my_x))
            dy = min(abs(py - my_y), global_map.height - abs(py - my_y))
            dist = dx + dy
            if dist < best_dist:
                best_dist = dist
                target_pos = (px, py)

    # -------------------------------------------------------------------------
    # 6. PATHFINDING & EMERGENCY TRAP SPLITTING
    # -------------------------------------------------------------------------
    chosen_move = None
    if target_pos:
        tx, ty = target_pos
        move = global_map.find_path_bfs(my_x, my_y, tx, ty, occupied_tiles)
        if move and is_safe_step(my_x, my_y, move, occupied_tiles, my_len):
            nx, ny = global_map.add_dir(my_x, my_y, move)
            space = flood_fill_space(nx, ny, occupied_tiles, my_len + 2)
            if space >= my_len:
                chosen_move = move
                
                # Broadcast pearl claim
                claim_msg = SonarMessage(
                    msg_type=MessageType.CLAIM_PEARL,
                    sender_x=my_x, sender_y=my_y,
                    target_x=tx, target_y=ty,
                    queen_x=queen_pos[0], queen_y=queen_pos[1],
                    queen_id=queen_id, sender_id=my_id
                )
                ct.send_sonar(Direction.NORTH, claim_msg.encode())

    # Fallback to safe step
    if not chosen_move:
        safe_moves = []
        for d in Direction.get_direction_list():
            if is_safe_step(my_x, my_y, d, occupied_tiles, my_len):
                nx, ny = global_map.add_dir(my_x, my_y, d)
                space = flood_fill_space(nx, ny, occupied_tiles, my_len + 1)
                if space >= my_len:
                    safe_moves.append(d)
        if safe_moves:
            chosen_move = random.choice(safe_moves)

    # -------------------------------------------------------------------------
    # 7. EMERGENCY DEAD-END SPLIT EXECUTION
    # -------------------------------------------------------------------------
    if not chosen_move and my_len >= 4:
        # Facing a trapped dead end! Split in half to shrink body volume instantly
        split_amount = my_len // 2
        if split_amount >= 2 and ct.can_split(split_amount):
            ct.output_log(f"Emergency dead-end split! Shedding {split_amount} tail segments.")
            ct.do_split(split_amount)
            return

    # Guaranteed move output
    if chosen_move:
        ct.make_move(chosen_move)
    else:
        # Desperation move into any non-kelp direction
        for d in Direction.get_direction_list():
            if global_map.edges.get(((my_x, my_y), d.value), EdgeType.EMPTY) != EdgeType.KELP:
                ct.make_move(d)
                return
        ct.make_move(Direction.NORTH)

    # Broadcast Queen GPS Pulse continuously
    if is_queen:
        hb_msg = SonarMessage(
            msg_type=MessageType.QUEEN_HEARTBEAT,
            sender_x=my_x, sender_y=my_y,
            target_x=0, target_y=0,
            queen_x=my_x, queen_y=my_y,
            queen_id=my_id, sender_id=my_id
        )
        ct.send_sonar(Direction.SOUTH, hb_msg.encode())

# =============================================================================
# MAIN LOOP
# =============================================================================
def main() -> None:
    global ct, game, global_map
    ct, game = unswbc.init()
    w, h = game.get_map_size()
    global_map = GlobalMap(w, h)
    
    while unswbc.update(ct, game):
        try:
            execute_turn()
        except Exception as e:
            import traceback
            for line in traceback.format_exc().split('\n'):
                if line:
                    ct.output_log(f"ERROR: {line}")
        unswbc.end_turn()

if __name__ == "__main__":
    main()
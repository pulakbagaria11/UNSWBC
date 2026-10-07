import helper as unswbc
from helper import Direction, EdgeType
import random
import collections

# =============================================================================
# 64-BIT OBFUSCATED QUAD-DIRECTIONAL SONAR PROTOCOL
# =============================================================================
class MessageType:
    PING = 0
    CLAIM_PEARL = 1
    ENEMY_SPOTTED = 2
    QUEEN_HEARTBEAT = 3
    DEAD_END_ALERT = 4

class SonarMessage:
    XOR_MASK = 0x3C9A5F12B7E481D0  # Obfuscation mask against enemy sniffers
    SEP = 0b11  # 2-bit field separator
    
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
        return raw ^ self.XOR_MASK

    @staticmethod
    def decode(val: int):
        raw = val ^ SonarMessage.XOR_MASK
        
        sep1 = (raw >> 58) & 0x3
        sep2 = (raw >> 44) & 0x3
        sep3 = (raw >> 30) & 0x3
        if sep1 != SonarMessage.SEP or sep2 != SonarMessage.SEP or sep3 != SonarMessage.SEP:
            return None  # Failed separator verification
            
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
# GLOBAL MAP, PORTAL & DEAD-END TRACKER
# =============================================================================
class GlobalMap:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.pearl_countdowns = {} 
        self.has_pearl = set()
        self.edges = {}
        self.portals = {}       # portal_id -> set of (x,y)
        self.dead_end_tiles = set()
        
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
            if (nx, ny) not in self.dead_end_tiles:
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
# BOT CONTROLLER & ADVANCED TACTICAL ENGINE
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
            if (nx, ny) not in visited and (nx, ny) not in occupied_tiles and (nx, ny) not in global_map.dead_end_tiles:
                visited.add((nx, ny))
                queue.append((nx, ny))
    return space

def is_safe_step(start_x, start_y, direction, occupied_tiles, my_len):
    edge_type = global_map.edges.get(((start_x, start_y), direction.value), EdgeType.EMPTY)
    
    # 1. Kelp check
    if edge_type == EdgeType.KELP:
        return False
        
    # 2. Portal destination inspection & dead-end space check
    if edge_type == EdgeType.PORTAL:
        portal_exit = global_map.get_portal_destination(start_x, start_y, direction)
        if portal_exit is not None:
            ex, ey = portal_exit
            if (ex, ey) in occupied_tiles or (ex, ey) in global_map.dead_end_tiles:
                return False
            exit_space = flood_fill_space(ex, ey, occupied_tiles, max_depth=my_len + 2)
            if exit_space < my_len + 2:
                global_map.dead_end_tiles.add((ex, ey))  # Flag portal destination as dead end
                return False
            return True

    # 3. Standard step check
    nx, ny = global_map.add_dir(start_x, start_y, direction)
    if (nx, ny) in occupied_tiles or (nx, ny) in global_map.dead_end_tiles:
        return False
        
    return True

def find_encirclement_tile(enemy_x, enemy_y, occupied_tiles):
    """Finds the best surrounding tile to loop around and trap a smaller enemy dragon."""
    best_tile = None
    max_walls = -1
    
    for d in Direction.get_direction_list():
        nx, ny = global_map.add_dir(enemy_x, enemy_y, d)
        if (nx, ny) not in occupied_tiles and (nx, ny) not in global_map.dead_end_tiles:
            # Count adjacent obstacles around this escape tile
            wall_count = 0
            for nd in Direction.get_direction_list():
                adj_x, adj_y = global_map.add_dir(nx, ny, nd)
                if (adj_x, adj_y) in occupied_tiles or global_map.edges.get(((nx, ny), nd.value), EdgeType.EMPTY) == EdgeType.KELP:
                    wall_count += 1
            if wall_count > max_walls:
                max_walls = wall_count
                best_tile = (nx, ny)
                
    return best_tile if best_tile else (enemy_x, enemy_y)

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
    # 1. QUAD-DIRECTIONAL SONAR INGESTION & DYNAMIC QUEEN SUCCESSION
    # -------------------------------------------------------------------------
    for msg_val in ct.get_sonar_messages():
        msg = SonarMessage.decode(msg_val)
        if msg:
            if msg.msg_type == MessageType.QUEEN_HEARTBEAT:
                if queen_id is None or msg.queen_id <= queen_id:
                    queen_id = msg.queen_id
                    queen_pos = (msg.queen_x, msg.queen_y)
                    queen_last_seen = current_turn
            elif msg.msg_type == MessageType.CLAIM_PEARL:
                claimed_pearls.add((msg.target_x, msg.target_y))
            elif msg.msg_type == MessageType.DEAD_END_ALERT:
                global_map.dead_end_tiles.add((msg.target_x, msg.target_y))

    # Reset Queen if heartbeat absent for > 3 turns (Queen death recovery)
    if (current_turn - queen_last_seen) > 3:
        queen_id = None

    # Election: Active dragon with lowest ID assumes Queen role
    if queen_id is None or my_id < queen_id:
        queen_id = my_id
        queen_pos = (my_x, my_y)
        queen_last_seen = current_turn

    is_queen = (my_id == queen_id)
    if is_queen:
        queen_pos = (my_x, my_y)

    # -------------------------------------------------------------------------
    # 2. LOCAL VISION SCAN (ENEMIES, QUEEN & DEAD-END PORTAL DETECTION)
    # -------------------------------------------------------------------------
    occupied_tiles = set()
    enemy_dragons = []  # (x, y, length)
    queen_head_nearby = None
    dead_end_portals = set()
    
    for tile in ct.get_tiles():
        if tile is None: 
            continue
        part = tile.get_dragon()
        tx, ty = tile.get_position().x, tile.get_position().y
        
        if part:
            occupied_tiles.add((tx, ty))
            if part.get_team() != my_team and part.is_head():
                enemy_dragons.append((tx, ty, part.get_length() if hasattr(part, 'get_length') else 3))
            elif part.get_team() == my_team and part.is_head() and part.get_id() == queen_id and not is_queen:
                queen_head_nearby = (tx, ty)

    # Inspect portals for dead-end "Dooring" opportunity
    for pid, p_set in global_map.portals.items():
        for px, py in p_set:
            space = flood_fill_space(px, py, occupied_tiles, max_depth=12)
            if space < 12:
                dead_end_portals.add((px, py))

    # -------------------------------------------------------------------------
    # 3. QUEEN INTERACTION ("BE FOOD" OR "LEAVE")
    # -------------------------------------------------------------------------
    if not is_queen and queen_head_nearby:
        qx, qy = queen_head_nearby
        dist_to_queen = abs(qx - my_x) + abs(qy - my_y)
        
        # A. "Be Food": Step directly into Queen's head if adjacent
        if dist_to_queen == 1:
            for d in Direction.get_direction_list():
                nx, ny = global_map.add_dir(my_x, my_y, d)
                if (nx, ny) == (qx, qy):
                    ct.output_log("Feeding the Queen!")
                    ct.make_move(d)
                    return

        # B. "Leave": Vacate area around Queen
        escape_moves = []
        for d in Direction.get_direction_list():
            if is_safe_step(my_x, my_y, d, occupied_tiles, my_len):
                nx, ny = global_map.add_dir(my_x, my_y, d)
                if (abs(nx - qx) + abs(ny - qy)) > dist_to_queen:
                    escape_moves.append(d)
        if escape_moves:
            ct.output_log("Yielding path to Queen!")
            ct.make_move(random.choice(escape_moves))
            return

    # -------------------------------------------------------------------------
    # 4. SWARM SPLITTING ECONOMICS (QUEEN IS STRICTLY EXCLUDED)
    # -------------------------------------------------------------------------
    # RULE 1: THE QUEEN DRAGON MUST NOT SPLIT!
    if not is_queen and my_len >= 5 and ct.get_unit_count() < 64:
        if ct.can_split(2):
            ct.output_log("Splitting new harvester drone!")
            ct.do_split(2)
            return

    # -------------------------------------------------------------------------
    # 5. TARGET SELECTION (DOOR DEAD PORTALS, ENCIRCLE ENEMIES, FARM PEARLS)
    # -------------------------------------------------------------------------
    target_pos = None

    # Priority A: "Door" / Block dead-end portals if enemies are nearby
    if dead_end_portals and enemy_dragons:
        target_pos = min(dead_end_portals, key=lambda p: abs(p[0] - my_x) + abs(p[1] - my_y))
        ct.output_log(f"Dooring dead-end portal at {target_pos}")

    # Priority B: Encircle / Loop smaller enemy snakes
    elif enemy_dragons:
        # Find smaller enemy snake head
        smaller_enemies = [e for e in enemy_dragons if e[2] < my_len]
        if smaller_enemies:
            ex, ey, elen = smaller_enemies[0]
            target_pos = find_encirclement_tile(ex, ey, occupied_tiles)
            ct.output_log(f"Encircling smaller enemy ({elen}) at {target_pos}")
        else:
            # Avoid larger enemy heads
            ex, ey, _ = enemy_dragons[0]
            target_pos = (ex, ey)

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
    # 6. PATHFINDING & DEAD-END AVOIDANCE
    # -------------------------------------------------------------------------
    chosen_move = None
    if target_pos:
        tx, ty = target_pos
        move = global_map.find_path_bfs(my_x, my_y, tx, ty, occupied_tiles)
        if move and is_safe_step(my_x, my_y, move, occupied_tiles, my_len):
            nx, ny = global_map.add_dir(my_x, my_y, move)
            space = flood_fill_space(nx, ny, occupied_tiles, max_depth=my_len + 2)
            if space >= my_len + 2:
                chosen_move = move
            else:
                # Flag tile as dead end and alert team via Sonar
                global_map.dead_end_tiles.add((nx, ny))
                dead_msg = SonarMessage(MessageType.DEAD_END_ALERT, my_x, my_y, nx, ny, 
                                        queen_pos[0], queen_pos[1], queen_id, my_id)
                for d in Direction.get_direction_list():
                    ct.send_sonar(d, dead_msg.encode())

    # Fallback to safe non-dead-end move
    if not chosen_move:
        safe_moves = []
        for d in Direction.get_direction_list():
            if is_safe_step(my_x, my_y, d, occupied_tiles, my_len):
                nx, ny = global_map.add_dir(my_x, my_y, d)
                space = flood_fill_space(nx, ny, occupied_tiles, max_depth=my_len + 2)
                if space >= my_len + 2:
                    safe_moves.append(d)
        if safe_moves:
            chosen_move = random.choice(safe_moves)

    # Emergency dead-end split for trapped non-queen drones
    if not chosen_move and not is_queen and my_len >= 4:
        split_amount = my_len // 2
        if split_amount >= 2 and ct.can_split(split_amount):
            ct.output_log(f"Emergency dead-end split! Shedding {split_amount} segments.")
            ct.do_split(split_amount)
            return

    # Guaranteed movement
    if chosen_move:
        ct.make_move(chosen_move)
    else:
        for d in Direction.get_direction_list():
            if global_map.edges.get(((my_x, my_y), d.value), EdgeType.EMPTY) != EdgeType.KELP:
                ct.make_move(d)
                return
        ct.make_move(Direction.NORTH)

    # -------------------------------------------------------------------------
    # 7. QUAD-DIRECTIONAL SONAR TRANSMISSION
    # -------------------------------------------------------------------------
    # Send sonar in ALL 4 cardinal directions (North, South, East, West)
    for d in Direction.get_direction_list():
        if is_queen:
            hb_msg = SonarMessage(
                msg_type=MessageType.QUEEN_HEARTBEAT,
                sender_x=my_x, sender_y=my_y,
                target_x=0, target_y=0,
                queen_x=my_x, queen_y=my_y,
                queen_id=my_id, sender_id=my_id
            )
            ct.send_sonar(d, hb_msg.encode())
        elif chosen_move and target_pos:
            claim_msg = SonarMessage(
                msg_type=MessageType.CLAIM_PEARL,
                sender_x=my_x, sender_y=my_y,
                target_x=target_pos[0], target_y=target_pos[1],
                queen_x=queen_pos[0], queen_y=queen_pos[1],
                queen_id=queen_id, sender_id=my_id
            )
            ct.send_sonar(d, claim_msg.encode())

# =============================================================================
# MAIN ENGINE ENTRY POINT
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
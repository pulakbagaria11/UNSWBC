import helper as unswbc
from helper import Direction, EdgeType
import random
import collections

# =============================================================================
# SONAR PROTOCOL
# =============================================================================
class MessageType:
    PING = 0
    CLAIM_PEARL = 1
    ENEMY_SPOTTED = 2
    QUEEN_HEARTBEAT = 3

class SonarMessage:
    MAGIC_KEY = 0xA5
    
    def __init__(self, msg_type, sender_x, sender_y, target_x, target_y, length, sender_id):
        self.msg_type = msg_type
        self.sender_x = sender_x
        self.sender_y = sender_y
        self.target_x = target_x
        self.target_y = target_y
        self.length = length
        self.sender_id = sender_id
        
    def encode(self) -> int:
        res = (self.MAGIC_KEY & 0xFF) << 56
        res |= (self.msg_type & 0x7) << 53
        res |= (self.sender_x & 0x3F) << 47
        res |= (self.sender_y & 0x3F) << 41
        res |= (self.target_x & 0x3F) << 35
        res |= (self.target_y & 0x3F) << 29
        res |= (self.length & 0x3F) << 23
        res |= (self.sender_id & 0x3F) << 17
        return res

    @staticmethod
    def decode(val: int):
        magic = (val >> 56) & 0xFF
        if magic != SonarMessage.MAGIC_KEY:
            return None
        msg_type = (val >> 53) & 0x7
        sender_x = (val >> 47) & 0x3F
        sender_y = (val >> 41) & 0x3F
        target_x = (val >> 35) & 0x3F
        target_y = (val >> 29) & 0x3F
        length = (val >> 23) & 0x3F
        sender_id = (val >> 17) & 0x3F
        return SonarMessage(msg_type, sender_x, sender_y, target_x, target_y, length, sender_id)

# =============================================================================
# GAME STATE
# =============================================================================
class GlobalMap:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        # -1 = unknown, otherwise countdown.
        self.pearl_countdowns = {} 
        self.has_pearl = set()
        
        # We track edges as ((x,y), direction) -> EdgeType
        self.edges = {}
        self.portals = {} # id -> list of ((x,y), dir)
        
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
                    pid = edge.get_portal_id()
                    if pid not in self.portals:
                        self.portals[pid] = set()
                    self.portals[pid].add(((x, y), d.value))

    def get_neighbors(self, x, y):
        neighbors = []
        for d in Direction.get_direction_list():
            edge_type = self.edges.get(((x, y), d.value), EdgeType.EMPTY)
            if edge_type == EdgeType.KELP:
                continue
            if edge_type == EdgeType.PORTAL:
                continue
            
            nx, ny = self.add_dir(x, y, d)
            neighbors.append((nx, ny, d))
        return neighbors

    def find_path_bfs(self, start_x, start_y, target_x, target_y, occupied_tiles):
        if start_x == target_x and start_y == target_y:
            return None
            
        queue = collections.deque()
        queue.append((start_x, start_y, None))
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
# Python Swarm & Hunter Bot
# Architecture: Tier 1 (Greedy Harvester) + Tier 2 (Sonar Swarm) + Tier 3 (Hunter)
# =============================================================================

ct: unswbc.Controller
game: unswbc.Game
global_map: GlobalMap

queen_id = None
claimed_pearls = set()

def is_safe_step(start_x, start_y, direction, occupied_tiles):
    edge_type = global_map.edges.get(((start_x, start_y), direction.value), EdgeType.EMPTY)
    if edge_type == EdgeType.KELP:
        return False
    nx, ny = global_map.add_dir(start_x, start_y, direction)
    if (nx, ny) in occupied_tiles:
        return False
    return True

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

def execute_turn() -> None:
    global queen_id, claimed_pearls
    claimed_pearls.clear()
    
    here = ct.get_position()
    my_x, my_y = here.x, here.y
    my_len = ct.get_length()
    my_id = ct.get_id()
    
    global_map.tick_round()
    global_map.update_from_vision(ct)
    
    # 1. Read Sonar
    for msg_val in ct.get_sonar_messages():
        msg = SonarMessage.decode(msg_val)
        if msg:
            if msg.msg_type == MessageType.CLAIM_PEARL:
                claimed_pearls.add((msg.target_x, msg.target_y))
            elif msg.msg_type == MessageType.QUEEN_HEARTBEAT:
                if queen_id is None or msg.sender_id < queen_id:
                    queen_id = msg.sender_id

    if queen_id is None or my_id < queen_id:
        queen_id = my_id

    # 2. Identify occupied tiles
    occupied_tiles = set()
    enemy_heads = []
    
    for tile in ct.get_tiles():
        if tile is None: continue
        part = tile.get_dragon()
        if part:
            tx, ty = tile.get_position().x, tile.get_position().y
            occupied_tiles.add((tx, ty))
            if part.get_team() != ct.get_team() and part.is_head():
                enemy_heads.append((tx, ty))
                
    # 3. Splitting Economics (Swarm Strategy)
    is_queen = (my_id == queen_id)
    if not is_queen and my_len >= 5 and ct.get_unit_count() < 64:
        ct.output_log("Spawning new harvester!")
        ct.do_split(2)
        return

    # 4. Target Selection (Pearls vs Hunting)
    target_pos = None
    if not is_queen and my_len <= 4 and enemy_heads:
        target_pos = enemy_heads[0]
        ct.output_log(f"Hunting enemy at {target_pos}")
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
                
    # 5. Pathfinding
    chosen_move = None
    if target_pos:
        tx, ty = target_pos
        move = global_map.find_path_bfs(my_x, my_y, tx, ty, occupied_tiles)
        if move and is_safe_step(my_x, my_y, move, occupied_tiles):
            nx, ny = global_map.add_dir(my_x, my_y, move)
            space = flood_fill_space(nx, ny, occupied_tiles, my_len + 2)
            if space >= my_len:
                chosen_move = move
                claim_msg = SonarMessage(MessageType.CLAIM_PEARL, my_x, my_y, tx, ty, my_len, my_id)
                ct.send_sonar(Direction.NORTH, claim_msg.encode())

    # Fallback to safe random step
    if not chosen_move:
        safe_moves = []
        for d in Direction.get_direction_list():
            if is_safe_step(my_x, my_y, d, occupied_tiles):
                nx, ny = global_map.add_dir(my_x, my_y, d)
                space = flood_fill_space(nx, ny, occupied_tiles, my_len + 1)
                if space >= my_len:
                    safe_moves.append(d)
        if safe_moves:
            chosen_move = random.choice(safe_moves)
        else:
            for d in Direction.get_direction_list():
                if is_safe_step(my_x, my_y, d, occupied_tiles):
                    chosen_move = d
                    break

    if chosen_move:
        ct.output_log(f"Moving {chosen_move.value}")
        ct.make_move(chosen_move)
    else:
        ct.output_log("No safe moves! Trapped.")
        ct.make_move(Direction.NORTH) 

    # Broadcast Queen heartbeat
    if is_queen:
        hb_msg = SonarMessage(MessageType.QUEEN_HEARTBEAT, my_x, my_y, 0, 0, my_len, my_id)
        ct.send_sonar(Direction.SOUTH, hb_msg.encode())

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
            tb = traceback.format_exc().split('\n')
            for line in tb:
                if line:
                    ct.output_log(f"ERROR: {line}")
        unswbc.end_turn()

if __name__ == "__main__":
    main()

"""
games.py — 9 Fully Playable Arcade Mini-Games

Each game opens in its own Pygame window. Close the window (or press ESC) to
return to the main app. Scores get saved to stats.json at the end + sound fx
come from assets/sfx (they silently do nothing if the files are missing).

Games:
1. Flappy Bird    — Spacebar to flap, dodge pipes
2. Snake          — Arrow keys, eat food, don't hit walls/tail
3. Tic-Tac-Toe    — Click cells, play vs AI bot (SPACE = rematch)
4. Memory Cards   — Click cards, find matching pairs (fewest moves wins)
5. Pong           — W/S or arrows, first to 5 points
6. 2048           — Arrow keys, merge tiles
7. Whack-a-Mole   — Click moles before they disappear, 30s timer
8. Breakout       — Mouse controls paddle, break all bricks
9. Dodge Runner   — Arrow keys, dodge falling obstacles
"""

import pygame
import random
import sys
import os
import math
import time

try:
    from ui import appdata
except Exception:
    appdata = None   # games still work without it, just no score saving

# ═══════════════════════════════════════════════════════════════
# SHARED UTILITIES
# ═══════════════════════════════════════════════════════════════

def create_window(title, w, h):
    """Create a pygame window with the given title and size."""
    pygame.init()   # makes sure pygame is ready (safe to call again)
    screen = pygame.display.set_mode((w, h))
    pygame.display.set_caption(title)
    return screen

def draw_text(screen, text, font, color, x, y, center=False):
    """Draw text on screen."""
    surface = font.render(text, True, color)
    rect = surface.get_rect()
    if center:
        rect.center = (x, y)
    else:
        rect.topleft = (x, y)
    screen.blit(surface, rect)
    return rect

def wait_for_key_or_close():
    """Event loop helper — returns True if window should close."""
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            return True
    return False

# ---- sound fx (best effort — never crashes, just goes quiet) ----

_SFX_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "sfx",
)
_sounds = {}
_sfx_volume = None

def _get_volume():
    """audio volume from settings.json (0-100). dynamically fetched, default 80%."""
    vol = 80
    try:
        if appdata is not None:
            vol = appdata.load_settings().get("volume", 80)
    except Exception:
        pass
    try:
        return max(0, min(100, int(vol))) / 100.0
    except Exception:
        return 0.8

def sfx(name):
    """play a sound effect. no mixer / missing file = silence, never a crash."""
    try:
        if not pygame.mixer.get_init():
            return
        if name not in _sounds:
            _sounds[name] = pygame.mixer.Sound(os.path.join(_SFX_DIR, name + ".wav"))
        _sounds[name].set_volume(_get_volume())
        _sounds[name].play()
    except Exception:
        pass

def _record_score(name, score, lower_better=False):
    if appdata is not None:
        appdata.record_score(name, score, lower_better)

def _record_win(name, win):
    if appdata is not None:
        appdata.record_win(name, win)

def _best_line(name, field="high"):
    """'Best: 12' for the game over screen (empty string if never played)."""
    try:
        if appdata is None:
            return ""
        entry = appdata.load_stats()["games"].get(name, {})
        v = entry.get(field, 0)
        return f"Best: {v}" if v else ""
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════
# TIC-TAC-TOE LOGIC (module level so it can be tested easily)
# ═══════════════════════════════════════════════════════════════

def check_win(b, p):
    """did player p win the board?"""
    for i in range(3):
        if all(b[i][j] == p for j in range(3)): return True
        if all(b[j][i] == p for j in range(3)): return True
    if all(b[i][i] == p for i in range(3)): return True
    if all(b[i][2-i] == p for i in range(3)): return True
    return False

def is_board_full(b):
    return all(b[i][j] != "" for i in range(3) for j in range(3))

def ai_move(b):
    """the bot's brain: 1. win if it can 2. block if u would win
    3. take center 4. take a corner. mutates b in place."""
    for p in ["O", "X"]:
        for i in range(3):
            for j in range(3):
                if b[i][j] == "":
                    b[i][j] = p
                    won = check_win(b, p)
                    b[i][j] = ""
                    if won:
                        b[i][j] = "O"   # take the winning cell (or block urs)
                        return
    if b[1][1] == "":
        b[1][1] = "O"
        return
    for i, j in [(0,0),(0,2),(2,0),(2,2),(0,1),(1,0),(1,2),(2,1)]:
        if b[i][j] == "":
            b[i][j] = "O"
            return


# ═══════════════════════════════════════════════════════════════
# 2048 LOGIC (module level so it can be tested easily)
# ═══════════════════════════════════════════════════════════════

GRID = 4

def slide_2048(row):
    """slide one row left + merge equal neighbours. returns (new_row, points)."""
    nums = [x for x in row if x != 0]
    result = []
    score = 0
    i = 0
    while i < len(nums):
        if i + 1 < len(nums) and nums[i] == nums[i+1]:
            result.append(nums[i] * 2)
            score += nums[i] * 2
            i += 2
        else:
            result.append(nums[i])
            i += 1
    result += [0] * (GRID - len(result))
    return result, score

def move_2048(b, direction):
    """apply a move to the board in place. returns (changed, points).
    changed is False when nothing moved (so no new tile should spawn)."""
    changed = False
    score = 0
    if direction == 'left':
        for i in range(GRID):
            new_row, s = slide_2048(b[i])
            if new_row != b[i]: changed = True
            b[i] = new_row
            score += s
    elif direction == 'right':
        for i in range(GRID):
            new_row, s = slide_2048(b[i][::-1])
            new_row = new_row[::-1]
            if new_row != b[i]: changed = True
            b[i] = new_row
            score += s
    elif direction == 'up':
        for j in range(GRID):
            col = [b[i][j] for i in range(GRID)]
            new_col, s = slide_2048(col)
            if new_col != col: changed = True
            for i in range(GRID):
                b[i][j] = new_col[i]
            score += s
    elif direction == 'down':
        for j in range(GRID):
            col = [b[i][j] for i in range(GRID)][::-1]
            slid, s = slide_2048(col)
            if slid != col: changed = True   # compare in reversed space (both)
            new_col = slid[::-1]
            for i in range(GRID):
                b[i][j] = new_col[i]
            score += s
    return changed, score

def can_move(b):
    """False when the board is full and nothing can merge = game over."""
    for i in range(GRID):
        for j in range(GRID):
            if b[i][j] == 0: return True
            if j < GRID - 1 and b[i][j] == b[i][j+1]: return True
            if i < GRID - 1 and b[i][j] == b[i+1][j]: return True
    return False


# ═══════════════════════════════════════════════════════════════
# GAME 1: FLAPPY BIRD
# ═══════════════════════════════════════════════════════════════

def play_flappy_bird():
    W, H = 400, 600
    screen = create_window("Flappy Bird — Kushal Studios", W, H)
    clock = pygame.time.Clock()
    font_s = pygame.font.SysFont("Arial", 24)
    font_l = pygame.font.SysFont("Arial", 48, bold=True)

    bird_y = H // 2
    bird_v = 0
    gravity = 0.4
    flap = -7
    pipes = []          # each pipe = [x, gap_top, gap_bottom, scored]
    pipe_timer = 0
    score = 0
    game_over = False

    BIRD_X = 80
    PIPE_W = 60
    GAP = 150

    def restart():
        nonlocal bird_y, bird_v, pipes, pipe_timer, score, game_over
        bird_y = H // 2
        bird_v = 0
        pipes = []
        pipe_timer = 0
        score = 0
        game_over = False

    while True:
        dt = clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.display.quit()
                return
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.display.quit()
                    return
                if event.key == pygame.K_SPACE:
                    if game_over:
                        restart()
                    else:
                        bird_v = flap
                        sfx("flap")

        if not game_over:
            bird_v += gravity
            bird_y += bird_v

            pipe_timer += dt
            if pipe_timer > 1500:
                pipe_timer = 0
                gap_y = random.randint(150, H - 150)
                pipes.append([W, gap_y - GAP // 2, gap_y + GAP // 2, False])

            for p in pipes:
                p[0] -= 3
            pipes = [p for p in pipes if p[0] > -PIPE_W]

            for p in pipes:
                if BIRD_X + 15 > p[0] and BIRD_X - 15 < p[0] + PIPE_W:
                    if bird_y - 15 < p[1] or bird_y + 15 > p[2]:
                        game_over = True
                        sfx("hit")
                        _record_score("Flappy Bird", score)
                # scoring: count the pipe once its right edge passes the bird
                # (the old == check never fired cuz pipes move 3px a frame lol)
                if not p[3] and p[0] + PIPE_W < BIRD_X:
                    p[3] = True
                    score += 1
                    sfx("score")

            if bird_y > H - 15 or bird_y < 15:
                if not game_over:
                    sfx("hit")
                    _record_score("Flappy Bird", score)
                game_over = True

        screen.fill((135, 206, 235))

        # Pipes
        for p in pipes:
            pygame.draw.rect(screen, (34, 139, 34), (p[0], 0, PIPE_W, p[1]))
            pygame.draw.rect(screen, (34, 139, 34), (p[0], p[2], PIPE_W, H - p[2]))

        # Bird
        pygame.draw.circle(screen, (255, 215, 0), (BIRD_X, int(bird_y)), 15)
        pygame.draw.circle(screen, (0, 0, 0), (BIRD_X + 5, int(bird_y) - 5), 3)

        # Score
        draw_text(screen, str(score), font_l, (255, 255, 255), W // 2, 50, center=True)

        if game_over:
            draw_text(screen, "GAME OVER", font_l, (255, 0, 0), W // 2, H // 2, center=True)
            draw_text(screen, f"Score: {score}   {_best_line('Flappy Bird')}",
                      font_s, (255, 255, 255), W // 2, H // 2 + 50, center=True)
            draw_text(screen, "Press SPACE to restart", font_s, (255, 255, 255),
                      W // 2, H // 2 + 80, center=True)

        pygame.display.flip()


# ═══════════════════════════════════════════════════════════════
# GAME 2: SNAKE
# ═══════════════════════════════════════════════════════════════

def play_snake():
    W, H = 400, 400
    CELL = 20
    COLS, ROWS = W // CELL, H // CELL
    screen = create_window("Snake — Kushal Studios", W, H)
    clock = pygame.time.Clock()
    font_s = pygame.font.SysFont("Arial", 20)
    font_l = pygame.font.SysFont("Arial", 36, bold=True)

    def random_food(body):
        # (takes the snake as a param now — the old version used the snake
        # variable before it existed and crashed the game on launch oops)
        while True:
            pos = (random.randint(0, COLS - 1), random.randint(0, ROWS - 1))
            if pos not in body:
                return pos

    def reset():
        body = [(COLS // 2, ROWS // 2)]
        return body, (1, 0), random_food(body), 0, False

    snake, direction, food, score, game_over = reset()
    move_timer = 0

    while True:
        dt = clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.display.quit()
                return
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.display.quit()
                    return
                if game_over and event.key == pygame.K_SPACE:
                    snake, direction, food, score, game_over = reset()
                if not game_over:
                    if event.key == pygame.K_UP and direction != (0, 1):
                        direction = (0, -1)
                    elif event.key == pygame.K_DOWN and direction != (0, -1):
                        direction = (0, 1)
                    elif event.key == pygame.K_LEFT and direction != (1, 0):
                        direction = (-1, 0)
                    elif event.key == pygame.K_RIGHT and direction != (-1, 0):
                        direction = (1, 0)

        if not game_over:
            move_timer += dt
            if move_timer > 120:
                move_timer = 0
                head = snake[0]
                new_head = (head[0] + direction[0], head[1] + direction[1])

                if new_head[0] < 0 or new_head[0] >= COLS or new_head[1] < 0 or new_head[1] >= ROWS:
                    game_over = True
                    sfx("hit")
                    _record_score("Snake", score)
                elif new_head in snake:
                    game_over = True
                    sfx("hit")
                    _record_score("Snake", score)
                else:
                    snake.insert(0, new_head)
                    if new_head == food:
                        score += 1
                        sfx("eat")
                        food = random_food(snake)
                    else:
                        snake.pop()

        screen.fill((30, 30, 30))

        # Food
        pygame.draw.rect(screen, (255, 0, 0), (food[0] * CELL, food[1] * CELL, CELL, CELL))

        # Snake
        for i, (x, y) in enumerate(snake):
            color = (0, 255, 0) if i == 0 else (0, 200, 0)
            pygame.draw.rect(screen, color, (x * CELL, y * CELL, CELL, CELL))

        draw_text(screen, f"Score: {score}", font_s, (255, 255, 255), 5, 5)

        if game_over:
            draw_text(screen, "GAME OVER", font_l, (255, 0, 0), W // 2, H // 2 - 20, center=True)
            draw_text(screen, f"Score: {score}   {_best_line('Snake')}",
                      font_s, (255, 255, 255), W // 2, H // 2 + 25, center=True)
            draw_text(screen, "SPACE to restart", font_s, (255, 255, 255), W // 2, H // 2 + 55, center=True)

        pygame.display.flip()


# ═══════════════════════════════════════════════════════════════
# GAME 3: TIC-TAC-TOE (vs AI)
# ═══════════════════════════════════════════════════════════════

def play_tictactoe():
    W, H = 400, 400
    screen = create_window("Tic-Tac-Toe — Kushal Studios", W, H)
    clock = pygame.time.Clock()
    font_l = pygame.font.SysFont("Arial", 60, bold=True)
    font_s = pygame.font.SysFont("Arial", 20)

    def reset():
        return [["" for _ in range(3)] for _ in range(3)], "X", None

    board, turn, winner = reset()

    while True:
        clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.display.quit()
                return
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.display.quit()
                    return
                if event.key == pygame.K_SPACE and winner:
                    board, turn, winner = reset()
            if event.type == pygame.MOUSEBUTTONDOWN and turn == "X" and not winner:
                mx, my = event.pos
                col, row = mx // (W // 3), my // (H // 3)
                if board[row][col] == "":
                    board[row][col] = "X"
                    sfx("click")
                    if check_win(board, "X"):
                        winner = "X"
                        sfx("win")
                        _record_win("Tic-Tac-Toe", True)
                    elif is_board_full(board):
                        winner = "Draw"
                    else:
                        turn = "O"

        if turn == "O" and not winner:
            ai_move(board)
            sfx("click")
            if check_win(board, "O"):
                winner = "O"
                sfx("hit")
                _record_win("Tic-Tac-Toe", False)
            elif is_board_full(board):
                winner = "Draw"
            else:
                turn = "X"

        screen.fill((240, 240, 240))

        # Grid
        for i in range(1, 3):
            pygame.draw.line(screen, (100, 100, 100), (i * W // 3, 0), (i * W // 3, H), 3)
            pygame.draw.line(screen, (100, 100, 100), (0, i * H // 3), (W, i * H // 3), 3)

        # Marks
        for i in range(3):
            for j in range(3):
                if board[i][j]:
                    color = (0, 100, 255) if board[i][j] == "X" else (255, 50, 50)
                    draw_text(screen, board[i][j], font_l, color,
                              j * W // 3 + W // 6, i * H // 3 + H // 6, center=True)

        if winner:
            msg = "You Win!" if winner == "X" else "Bot Wins!" if winner == "O" else "Draw!"
            draw_text(screen, msg, font_s, (0, 0, 0), W // 2, H - 40, center=True)
            draw_text(screen, "SPACE for rematch", font_s, (120, 120, 120), W // 2, H - 20, center=True)

        pygame.display.flip()


# ═══════════════════════════════════════════════════════════════
# GAME 4: MEMORY CARD MATCH
# ═══════════════════════════════════════════════════════════════

def play_memory_cards():
    W, H = 400, 400
    screen = create_window("Memory Match — Kushal Studios", W, H)
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Arial", 36, bold=True)
    font_s = pygame.font.SysFont("Arial", 18)

    # card symbols instead of emoji (emoji showed up as empty boxes
    # on windows so symbols it is lol)
    symbols = ["♠", "♥", "♦", "♣", "●", "▲", "■", "◆"]

    card_w, card_h = 80, 80
    gap = 10
    start_x = (W - (4 * card_w + 3 * gap)) // 2
    start_y = (H - (4 * card_h + 3 * gap)) // 2 + 20

    def deal():
        cards = symbols * 2
        random.shuffle(cards)
        return cards, [False] * 16, [False] * 16

    cards, flipped, matched = deal()
    first = None
    moves = 0
    freeze = False
    freeze_timer = 0
    best_line = _best_line("Memory Match", "best")

    while True:
        dt = clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.display.quit()
                return
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.display.quit()
                    return
                if event.key == pygame.K_SPACE and all(matched):
                    cards, flipped, matched = deal()
                    first = None
                    moves = 0
                    best_line = _best_line("Memory Match", "best")
            if event.type == pygame.MOUSEBUTTONDOWN and not freeze and not all(matched):
                mx, my = event.pos
                for i in range(16):
                    col, row = i % 4, i // 4
                    cx = start_x + col * (card_w + gap)
                    cy = start_y + row * (card_h + gap)
                    if cx <= mx <= cx + card_w and cy <= my <= cy + card_h:
                        if not flipped[i] and not matched[i]:
                            flipped[i] = True
                            sfx("click")
                            if first is None:
                                first = i
                            else:
                                moves += 1
                                if cards[first] == cards[i]:
                                    matched[first] = True
                                    matched[i] = True
                                    if all(matched):
                                        sfx("win")
                                        _record_score("Memory Match", moves, lower_better=True)
                                        best_line = _best_line("Memory Match", "best")
                                    else:
                                        sfx("match")
                                else:
                                    freeze = True
                                    freeze_timer = 0
                                first = None

        if freeze:
            freeze_timer += dt
            if freeze_timer > 800:
                for i in range(16):
                    if flipped[i] and not matched[i]:
                        flipped[i] = False
                freeze = False

        screen.fill((30, 30, 40))

        for i in range(16):
            col, row = i % 4, i // 4
            cx = start_x + col * (card_w + gap)
            cy = start_y + row * (card_h + gap)

            if flipped[i] or matched[i]:
                pygame.draw.rect(screen, (100, 200, 255), (cx, cy, card_w, card_h), border_radius=8)
                draw_text(screen, cards[i], font, (255, 255, 255), cx + card_w // 2, cy + card_h // 2, center=True)
            else:
                pygame.draw.rect(screen, (60, 60, 80), (cx, cy, card_w, card_h), border_radius=8)
                draw_text(screen, "?", font, (150, 150, 170), cx + card_w // 2, cy + card_h // 2, center=True)

        draw_text(screen, f"Moves: {moves}   {best_line}", font_s, (255, 255, 255), 10, 5)

        if all(matched):
            draw_text(screen, "You Win!", font_s, (0, 255, 0), W // 2 - 60, H - 15)
            draw_text(screen, "SPACE to play again", font_s, (150, 150, 170), W // 2 + 60, H - 15)

        pygame.display.flip()


# ═══════════════════════════════════════════════════════════════
# GAME 5: PONG
# ═══════════════════════════════════════════════════════════════

def play_pong():
    W, H = 600, 400
    WIN_SCORE = 5
    screen = create_window("Pong — Kushal Studios", W, H)
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Arial", 24, bold=True)
    font_s = pygame.font.SysFont("Arial", 20)

    paddle_h = 80
    paddle_w = 10
    player_y = H // 2
    ai_y = H // 2
    ball_x, ball_y = W // 2, H // 2
    ball_vx, ball_vy = 4, 3
    player_score = 0
    ai_score = 0
    winner = None   # "You" / "Bot" once someone hits WIN_SCORE

    def reset_ball(direction):
        nonlocal ball_x, ball_y, ball_vx, ball_vy
        ball_x, ball_y = W // 2, H // 2
        ball_vx = 4 * direction
        ball_vy = random.choice([-3, 3])

    while True:
        clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.display.quit()
                return
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.display.quit()
                    return
                if event.key == pygame.K_SPACE and winner:
                    player_score = 0
                    ai_score = 0
                    winner = None
                    reset_ball(1)

        if not winner:
            keys = pygame.key.get_pressed()
            if keys[pygame.K_w] or keys[pygame.K_UP]:
                player_y -= 5
            if keys[pygame.K_s] or keys[pygame.K_DOWN]:
                player_y += 5
            player_y = max(0, min(H - paddle_h, player_y))

            ball_x += ball_vx
            ball_y += ball_vy

            if ball_y <= 0 or ball_y >= H:
                ball_vy *= -1

            # Player paddle collision (speed capped at 11 so the ball cant
            # start tunneling thru the paddles at max speed)
            if ball_x <= 20 and player_y <= ball_y <= player_y + paddle_h:
                ball_vx = min(abs(ball_vx) + 0.2, 11)
                sfx("click")
            # AI paddle collision
            if ball_x >= W - 20 and ai_y <= ball_y <= ai_y + paddle_h:
                ball_vx = -min(abs(ball_vx) + 0.2, 11)
                sfx("click")

            # Score
            if ball_x < 0:
                ai_score += 1
                sfx("score")
                reset_ball(1)
            if ball_x > W:
                player_score += 1
                sfx("score")
                reset_ball(-1)

            if player_score >= WIN_SCORE or ai_score >= WIN_SCORE:
                winner = "You" if player_score > ai_score else "Bot"
                if winner == "You":
                    sfx("win")
                else:
                    sfx("hit")
                _record_score("Pong", player_score)

            # AI movement
            if ai_y + paddle_h // 2 < ball_y:
                ai_y += 3.5
            elif ai_y + paddle_h // 2 > ball_y:
                ai_y -= 3.5
            ai_y = max(0, min(H - paddle_h, ai_y))

        screen.fill((0, 0, 0))
        pygame.draw.rect(screen, (255, 255, 255), (10, player_y, paddle_w, paddle_h))
        pygame.draw.rect(screen, (255, 255, 255), (W - 20, ai_y, paddle_w, paddle_h))
        pygame.draw.circle(screen, (255, 255, 255), (int(ball_x), int(ball_y)), 8)
        pygame.draw.line(screen, (100, 100, 100), (W // 2, 0), (W // 2, H), 2)
        draw_text(screen, str(player_score), font, (255, 255, 255), W // 4, 20, center=True)
        draw_text(screen, str(ai_score), font, (255, 255, 255), 3 * W // 4, 20, center=True)
        draw_text(screen, f"first to {WIN_SCORE}", font_s, (120, 120, 120), W // 2, 20, center=True)

        if winner:
            draw_text(screen, f"{winner} Win{'s' if winner == 'Bot' else ''}!",
                      font, (0, 255, 0) if winner == "You" else (255, 60, 60),
                      W // 2, H // 2, center=True)
            draw_text(screen, f"Final: {player_score} - {ai_score}   {_best_line('Pong')}",
                      font_s, (200, 200, 200), W // 2, H // 2 + 40, center=True)
            draw_text(screen, "SPACE for rematch", font_s, (120, 120, 120), W // 2, H // 2 + 65, center=True)

        pygame.display.flip()


# ═══════════════════════════════════════════════════════════════
# GAME 6: 2048
# ═══════════════════════════════════════════════════════════════

def play_2048():
    W, H = 400, 450
    screen = create_window("2048 — Kushal Studios", W, H)
    clock = pygame.time.Clock()
    font_s = pygame.font.SysFont("Arial", 18)
    # font cache — building 16 SysFonts every frame made the game laggy af
    tile_fonts = {
        28: pygame.font.SysFont("Arial", 28, bold=True),
        22: pygame.font.SysFont("Arial", 22, bold=True),
        18: pygame.font.SysFont("Arial", 18, bold=True),
    }

    CELL = 80
    GAP = 10
    BOARD_SIZE = GRID * CELL + (GRID + 1) * GAP
    BOARD_X = (W - BOARD_SIZE) // 2
    BOARD_Y = 50

    COLORS = {2: (238,228,218), 4: (237,224,200), 8: (242,177,121), 16: (245,149,99),
              32: (246,124,95), 64: (246,94,59), 128: (237,207,114), 256: (237,204,97),
              512: (237,200,80), 1024: (237,197,63), 2048: (237,194,46)}
    TEXT_COLORS = {2: (119,110,101), 4: (119,110,101)}

    def new_game():
        b = [[0]*GRID for _ in range(GRID)]
        for _ in range(2):
            add_random(b)
        return b, 0

    def add_random(b):
        empty = [(i,j) for i in range(GRID) for j in range(GRID) if b[i][j] == 0]
        if empty:
            i, j = random.choice(empty)
            b[i][j] = 2 if random.random() < 0.9 else 4

    board, score = new_game()
    game_over = False
    made_2048 = False

    while True:
        clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.display.quit()
                return
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.display.quit()
                    return
                if event.key == pygame.K_SPACE and game_over:
                    board, score = new_game()
                    game_over = False
                    made_2048 = False
                if not game_over:
                    direction = None
                    if event.key == pygame.K_LEFT: direction = 'left'
                    elif event.key == pygame.K_RIGHT: direction = 'right'
                    elif event.key == pygame.K_UP: direction = 'up'
                    elif event.key == pygame.K_DOWN: direction = 'down'
                    if direction:
                        changed, s = move_2048(board, direction)
                        if changed:
                            score += s
                            add_random(board)
                            sfx("click")
                            if not made_2048 and any(2048 in row for row in board):
                                made_2048 = True
                                sfx("win")
                                _record_score("2048", score)
                            if not can_move(board):
                                game_over = True
                                sfx("hit")
                                _record_score("2048", score)

        screen.fill((250, 248, 239))

        # Board background
        pygame.draw.rect(screen, (187, 173, 160), (BOARD_X, BOARD_Y, BOARD_SIZE, BOARD_SIZE), border_radius=6)

        # Tiles
        for i in range(GRID):
            for j in range(GRID):
                x = BOARD_X + GAP + j * (CELL + GAP)
                y = BOARD_Y + GAP + i * (CELL + GAP)
                val = board[i][j]
                color = COLORS.get(val, (60, 58, 50))
                pygame.draw.rect(screen, color, (x, y, CELL, CELL), border_radius=4)
                if val:
                    tc = TEXT_COLORS.get(val, (249, 246, 242))
                    fs = 28 if val < 100 else 22 if val < 1000 else 18
                    draw_text(screen, str(val), tile_fonts[fs], tc, x + CELL // 2, y + CELL // 2, center=True)

        draw_text(screen, f"Score: {score}   {_best_line('2048')}", font_s, (119, 110, 101), W // 2, 20, center=True)

        if made_2048 and not game_over:
            draw_text(screen, "2048!! keep going", font_s, (237, 194, 46), W // 2, 30, center=True)
        if game_over:
            draw_text(screen, "NO MOVES LEFT", font_s, (255, 0, 0), W // 2, 30, center=True)
            draw_text(screen, "GAME OVER — SPACE to restart", font_s, (255, 0, 0), W // 2, H - 20, center=True)

        pygame.display.flip()


# ═══════════════════════════════════════════════════════════════
# GAME 7: WHACK-A-MOLE
# ═══════════════════════════════════════════════════════════════

def play_whack_mole():
    W, H = 400, 500
    screen = create_window("Whack-a-Mole — Kushal Studios", W, H)
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Arial", 28, bold=True)
    font_s = pygame.font.SysFont("Arial", 20)

    HOLES = 9
    COLS, ROWS = 3, 3
    HOLE_W = 80
    GAP = 20
    START_X = (W - (COLS * HOLE_W + (COLS - 1) * GAP)) // 2
    START_Y = 80

    moles = [False] * HOLES
    score = 0
    time_left = 30.0
    mole_timer = 0
    game_over = False
    best_line = _best_line("Whack-a-Mole")

    def restart():
        nonlocal moles, score, time_left, mole_timer, game_over, best_line
        moles = [False] * HOLES
        score = 0
        time_left = 30.0
        mole_timer = 0
        game_over = False
        best_line = _best_line("Whack-a-Mole")

    while True:
        dt = clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.display.quit()
                return
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.display.quit()
                    return
                if event.key == pygame.K_SPACE and game_over:
                    restart()
            if event.type == pygame.MOUSEBUTTONDOWN and not game_over:
                mx, my = event.pos
                for i in range(HOLES):
                    col, row = i % COLS, i // COLS
                    hx = START_X + col * (HOLE_W + GAP)
                    hy = START_Y + row * (HOLE_W + GAP)
                    if hx <= mx <= hx + HOLE_W and hy <= my <= hy + HOLE_W and moles[i]:
                        moles[i] = False
                        score += 1
                        sfx("score")

        if not game_over:
            time_left -= dt / 1000
            if time_left <= 0:
                time_left = 0
                game_over = True
                sfx("hit")
                _record_score("Whack-a-Mole", score)

            mole_timer += dt
            if mole_timer > 800:
                mole_timer = 0
                idx = random.randint(0, HOLES - 1)
                moles[idx] = True
                # Hide some random moles
                for i in range(HOLES):
                    if random.random() < 0.3:
                        moles[i] = False

        screen.fill((34, 139, 34))

        for i in range(HOLES):
            col, row = i % COLS, i // COLS
            hx = START_X + col * (HOLE_W + GAP)
            hy = START_Y + row * (HOLE_W + GAP)
            pygame.draw.ellipse(screen, (101, 67, 33), (hx, hy, HOLE_W, HOLE_W))
            if moles[i]:
                pygame.draw.circle(screen, (160, 82, 45), (hx + HOLE_W // 2, hy + HOLE_W // 2), HOLE_W // 3)
                pygame.draw.circle(screen, (0, 0, 0), (hx + HOLE_W // 2 - 8, hy + HOLE_W // 2 - 5), 4)
                pygame.draw.circle(screen, (0, 0, 0), (hx + HOLE_W // 2 + 8, hy + HOLE_W // 2 - 5), 4)

        draw_text(screen, f"Score: {score}", font, (255, 255, 255), W // 2, 20, center=True)
        draw_text(screen, f"Time: {time_left:.1f}   {best_line}", font_s, (255, 255, 255), W // 2, 50, center=True)

        if game_over:
            draw_text(screen, "TIME'S UP!", font, (255, 0, 0), W // 2, H // 2, center=True)
            draw_text(screen, f"Final Score: {score}", font_s, (255, 255, 255), W // 2, H // 2 + 40, center=True)
            draw_text(screen, "SPACE to play again", font_s, (200, 200, 200), W // 2, H // 2 + 70, center=True)

        pygame.display.flip()


# ═══════════════════════════════════════════════════════════════
# GAME 8: BREAKOUT
# ═══════════════════════════════════════════════════════════════

def play_breakout():
    W, H = 480, 600
    screen = create_window("Breakout — Kushal Studios", W, H)
    clock = pygame.time.Clock()
    font_s = pygame.font.SysFont("Arial", 20)
    font_l = pygame.font.SysFont("Arial", 36, bold=True)

    paddle_w = 80
    paddle_h = 10
    brick_w = 40
    brick_h = 15
    brick_gap = 4

    def new_bricks():
        bricks = []
        for row in range(5):
            for col in range(10):
                bricks.append(pygame.Rect(col * (brick_w + brick_gap) + 10,
                                          50 + row * (brick_h + brick_gap),
                                          brick_w, brick_h))
        return bricks

    running = True
    while running:
        # ---- one match ----
        bricks = new_bricks()
        paddle_x = W // 2
        ball_x, ball_y = W // 2, H - 40
        ball_vx, ball_vy = 3, -3
        score = 0
        lives = 3
        result = None   # None / "win" / "lose"

        while True:
            clock.tick(60)

            restart_match = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.display.quit()
                    return
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        pygame.display.quit()
                        return
                    if event.key == pygame.K_SPACE and result:
                        restart_match = True

            if restart_match:
                break   # new match

            if not result:
                paddle_x = pygame.mouse.get_pos()[0] - paddle_w // 2
                paddle_x = max(0, min(W - paddle_w, paddle_x))

                ball_x += ball_vx
                ball_y += ball_vy

                if ball_x <= 0 or ball_x >= W: ball_vx *= -1
                if ball_y <= 0: ball_vy *= -1

                # Paddle collision
                if ball_y >= H - 20 and paddle_x <= ball_x <= paddle_x + paddle_w:
                    ball_vy = -abs(ball_vy)
                    sfx("click")

                # Brick collision
                for brick in bricks[:]:
                    if brick.collidepoint(ball_x, ball_y):
                        bricks.remove(brick)
                        ball_vy *= -1
                        score += 10
                        sfx("score")
                        break

                # Ball below paddle
                if ball_y > H:
                    lives -= 1
                    sfx("hit")
                    ball_x, ball_y = W // 2, H - 40
                    ball_vx, ball_vy = 3, -3
                    if lives <= 0:
                        result = "lose"
                        _record_score("Breakout", score)

                # Cleared the whole board (the old version just kept
                # bouncing forever with no bricks lol)
                if not bricks:
                    result = "win"
                    sfx("win")
                    _record_score("Breakout", score)

            screen.fill((0, 0, 0))
            pygame.draw.rect(screen, (100, 100, 255), (paddle_x, H - 15, paddle_w, paddle_h))
            pygame.draw.circle(screen, (255, 255, 255), (int(ball_x), int(ball_y)), 6)
            for brick in bricks:
                pygame.draw.rect(screen, (255, 100, 100), brick)
            draw_text(screen, f"Score: {score}  Lives: {lives}", font_s, (255, 255, 255), 5, 5)

            if result == "win":
                draw_text(screen, "YOU WIN!", font_l, (0, 255, 0), W // 2, H // 2, center=True)
            elif result == "lose":
                draw_text(screen, "GAME OVER", font_l, (255, 0, 0), W // 2, H // 2, center=True)

            if result:
                draw_text(screen, f"Score: {score}   {_best_line('Breakout')}",
                          font_s, (255, 255, 255), W // 2, H // 2 + 50, center=True)
                draw_text(screen, "SPACE to play again", font_s, (200, 200, 200),
                          W // 2, H // 2 + 80, center=True)

            pygame.display.flip()


# ═══════════════════════════════════════════════════════════════
# GAME 9: DODGE RUNNER
# ═══════════════════════════════════════════════════════════════

def play_dodge_runner():
    W, H = 400, 500
    screen = create_window("Dodge Runner — Kushal Studios", W, H)
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Arial", 24, bold=True)
    font_s = pygame.font.SysFont("Arial", 16)

    player_x = W // 2
    player_w = 30
    player_h = 30
    obstacles = []   # each = [x, y, w, h]
    obs_timer = 0
    speed = 3
    score = 0
    game_over = False

    def restart():
        nonlocal player_x, obstacles, obs_timer, speed, score, game_over
        player_x = W // 2
        obstacles = []
        obs_timer = 0
        speed = 3
        score = 0
        game_over = False

    while True:
        dt = clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.display.quit()
                return
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.display.quit()
                    return
                if game_over and event.key == pygame.K_SPACE:
                    restart()

        if not game_over:
            keys = pygame.key.get_pressed()
            if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                player_x -= 5
            if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                player_x += 5
            player_x = max(0, min(W - player_w, player_x))

            obs_timer += dt
            if obs_timer > 800:
                obs_timer = 0
                obstacles.append([random.randint(0, W - 25), 0, 25, 25])

            for obs in obstacles:
                obs[1] += speed
            obstacles = [o for o in obstacles if o[1] < H]

            score += 1
            if score % 200 == 0:
                speed += 0.5

            # Collision — proper rect vs rect now (the old version only
            # checked the obstacle's corner point so u could clip thru
            # like half of it and not die)
            player_rect = pygame.Rect(player_x, H - 50, player_w, player_h)
            for obs in obstacles:
                if player_rect.colliderect(pygame.Rect(obs[0], obs[1], obs[2], obs[3])):
                    game_over = True
                    sfx("hit")
                    _record_score("Dodge Runner", score)
                    break

        screen.fill((20, 20, 30))

        # Player
        pygame.draw.rect(screen, (0, 255, 100), (player_x, H - 50, player_w, player_h))

        # Obstacles
        for obs in obstacles:
            pygame.draw.rect(screen, (255, 50, 50), (obs[0], obs[1], obs[2], obs[3]))

        draw_text(screen, f"Score: {score}", font, (255, 255, 255), 10, 10)

        if game_over:
            draw_text(screen, "GAME OVER", font, (255, 0, 0), W // 2, H // 2, center=True)
            draw_text(screen, f"Score: {score}   {_best_line('Dodge Runner')}",
                      font_s, (255, 255, 255), W // 2, H // 2 + 35, center=True)
            draw_text(screen, "SPACE to restart", font_s, (255, 255, 255), W // 2, H // 2 + 60, center=True)

        pygame.display.flip()


# ═══════════════════════════════════════════════════════════════
# GAME REGISTRY
# ═══════════════════════════════════════════════════════════════

GAMES = [
    {"name": "Flappy Bird",  "icon": "🐤", "desc": "spacebar to flap",     "func": play_flappy_bird},
    {"name": "Snake",        "icon": "🐍", "desc": "eat n grow",           "func": play_snake},
    {"name": "Tic-Tac-Toe",  "icon": "❌", "desc": "beat the bot",         "func": play_tictactoe},
    {"name": "Memory Match", "icon": "🧠", "desc": "find the pairs",       "func": play_memory_cards},
    {"name": "Pong",         "icon": "🏓", "desc": "first to 5 points",    "func": play_pong},
    {"name": "2048",         "icon": "🧩", "desc": "merge tiles",          "func": play_2048},
    {"name": "Whack-a-Mole", "icon": "🔨", "desc": "30s mole hunt",        "func": play_whack_mole},
    {"name": "Breakout",     "icon": "🧱", "desc": "break em all",         "func": play_breakout},
    {"name": "Dodge Runner", "icon": "🏃", "desc": "dodge everything",     "func": play_dodge_runner},
]


def launch_game(index: int):
    """Launch a game by index (0-8). Opens in its own pygame window."""
    if 0 <= index < len(GAMES):
        pygame.init()
        GAMES[index]["func"]()
        pygame.display.quit()

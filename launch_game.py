"""
launch_game.py — Standalone game launcher (called as subprocess by main app)

Usage: python launch_game.py <game_index>
Opens a pygame window for the selected game. Closes when the game window is closed.
"""
import sys
import os

# Under pythonw.exe, sys.stdout and sys.stderr are None; redirect to devnull so
# print() and library loggers do not crash with AttributeError.
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.games import GAMES

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python launch_game.py <game_index>")
        sys.exit(1)

    # bad index should just quit with a message, not a scary traceback
    try:
        idx = int(sys.argv[1])
    except ValueError:
        print(f"not a number: {sys.argv[1]}")
        sys.exit(1)

    if 0 <= idx < len(GAMES):
        print(f"starting {GAMES[idx]['name']}...")
        import pygame
        pygame.init()
        GAMES[idx]["func"]()
        pygame.display.quit()
        pygame.quit()
    else:
        print(f"no game with index {idx} (0-{len(GAMES) - 1})")
        sys.exit(1)

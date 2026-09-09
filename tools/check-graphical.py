"""Headless native UI geometry and keyboard checks, no hardware actions."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from graphical import GraphicalScreen
import curses
from PIL import ImageGrab

project = Path(__file__).resolve().parents[1]
screen = GraphicalScreen(windowed=True)
results = []
try:
    for width, height in ((640, 480), (800, 600), (1280, 800), (1920, 1080)):
        screen.root.geometry(f'{width}x{height}+0+0')
        screen.root.update()
        screen.root.focus_force()
        screen.root.update()
        rows = ['1. Запустить систему', '2. Инструменты', '3. Диагностика', '4. Питание',
                '5. Настройки голоса', '6. Справка']
        screen.render('Главное меню', rows, 1, 'Громкость 75%.')
        screen.root.update()
        path = project / f'dist/graphical-{width}x{height}.png'
        ImageGrab.grab(bbox=(0, 0, width, height), xdisplay=os.environ['DISPLAY']).save(path)
        assert screen.regions and all(0 <= top < bottom < height - 90 for top, bottom, _ in screen.regions)
        for keysym, expected in [('Down', curses.KEY_DOWN), ('F3', curses.KEY_F3),
                                 ('Delete', curses.KEY_DC), ('Insert', curses.KEY_IC),
                                 ('Home', curses.KEY_HOME), ('End', curses.KEY_END),
                                 ('KP_End', curses.KEY_END), ('Next', curses.KEY_NPAGE),
                                 ('Prior', curses.KEY_PPAGE)]:
            screen.root.event_generate('<KeyPress-' + keysym + '>', when='tail')
            actual = screen.getch()
            assert actual == expected, (keysym, actual, expected, screen.root.focus_get())
        results.append({'width': width, 'height': height, 'keys': 'pass', 'rows': len(screen.regions)})
        screen.render('Целевой раздел, серийный номер и UUID. ' * 16 + 'Все данные будут заменены.',
                      ['1. Отмена', '2. Продолжить'], 0)
        screen.root.update()
        assert len(screen.regions) == 2 and all(bottom < height - 90 for _, bottom, _ in screen.regions)
        screen.activate(SimpleNamespace(x=width // 2, y=30))
        screen.activate(SimpleNamespace(x=1, y=screen.regions[1][0] + 5))
        assert not screen.queue, 'Blank area activated confirmation'
        screen.activate(SimpleNamespace(x=width // 2, y=screen.regions[1][0] + 5))
        assert list(screen.queue) == [curses.KEY_HOME, curses.KEY_DOWN, 10]
        screen.queue.clear()
        ImageGrab.grab(bbox=(0, 0, width, height), xdisplay=os.environ['DISPLAY']).save(project / f'dist/graphical-confirm-{width}.png')
    (project / 'dist/graphical-verification.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results))
finally:
    screen.close()

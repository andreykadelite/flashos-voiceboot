"""Keep USB help aligned with the speaking help content."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from help_content import TOPICS, VERSION

root = Path(__file__).resolve().parents[1]
text = '# FlashOS: инструкция\n\nВерсия ' + VERSION + '.\n\n'
text += '\n\n'.join('## ' + title + '\n\n' + '\n\n'.join(rows) for title, rows in TOPICS) + '\n'
(root / 'docs/HELP-RU.md').write_text(text, encoding='utf-8')

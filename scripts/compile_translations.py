"""Compile this project's UTF-8 gettext catalogs without external programs.

Run `python scripts/compile_translations.py` after editing django.po. Standard
`django-admin compilemessages` is also supported when GNU gettext is installed.
This small compiler handles ordinary gettext messages, plurals and contexts.
"""
import ast
import struct
from pathlib import Path


def read_catalog(path):
    messages = {}
    entry, active, fuzzy = {}, None, False

    def finish():
        if 'msgid' in entry and not fuzzy:
            key = entry['msgid']
            if 'msgctxt' in entry:
                key = entry['msgctxt'] + '\x04' + key
            if 'msgid_plural' in entry:
                key += '\0' + entry['msgid_plural']
                indexes = [int(field[7:-1]) for field in entry if field.startswith('msgstr[')]
                forms = max(indexes, default=-1) + 1
                value = '\0'.join(entry.get(f'msgstr[{i}]', '') for i in range(forms))
            else:
                value = entry.get('msgstr', '')
            if value:
                messages[key] = value

    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines() + [''], 1):
        line = line.strip()
        if not line:
            finish()
            entry, active, fuzzy = {}, None, False
        elif line.startswith('#'):
            fuzzy = fuzzy or (line.startswith('#,') and 'fuzzy' in line)
        elif line.startswith('"'):
            if active is None:
                raise ValueError(f'{path}:{number}: string without a field')
            entry[active] += ast.literal_eval(line)
        else:
            active, quoted = line.split(None, 1)
            if active not in {'msgctxt', 'msgid', 'msgid_plural', 'msgstr'} and not active.startswith('msgstr['):
                raise ValueError(f'{path}:{number}: unsupported field {active}')
            entry[active] = ast.literal_eval(quoted)
    return messages


def compile_catalog(path):
    messages = read_catalog(path)
    keys = sorted(messages)
    originals = [key.encode('utf-8') for key in keys]
    translated = [messages[key].encode('utf-8') for key in keys]
    count = len(keys)
    start = 28 + count * 16
    original_data = b'\0'.join(originals) + b'\0'
    translated_data = b'\0'.join(translated) + b'\0'
    original_offsets, translated_offsets = [], []
    offset = start
    for item in originals:
        original_offsets.extend((len(item), offset))
        offset += len(item) + 1
    for item in translated:
        translated_offsets.extend((len(item), offset))
        offset += len(item) + 1
    header = struct.pack('<7I', 0x950412DE, 0, count, 28, 28 + count * 8, 0, 0)
    tables = struct.pack(f'<{count * 4}I', *original_offsets, *translated_offsets)
    output = path.with_suffix('.mo')
    output.write_bytes(header + tables + original_data + translated_data)
    print(f'{path.relative_to(Path(__file__).resolve().parents[1])}: {count} messages')


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    for catalog in sorted((root / 'locale').glob('*/LC_MESSAGES/*.po')):
        compile_catalog(catalog)

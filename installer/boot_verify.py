"""Verify Ventoy boot code, partition tables and every shipped EFI-volume file."""
import hashlib
import lzma
import struct
import zlib
import uuid
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def gpt_header(read, lba, sectors):
    raw = read(lba * 512, 512)
    require(raw[:8] == b'EFI PART', 'Повреждён заголовок GPT.')
    size, checksum = struct.unpack_from('<II', raw, 12)
    require(92 <= size <= 512, 'Недопустимый размер заголовка GPT.')
    checked = bytearray(raw[:size])
    checked[16:20] = b'\0' * 4
    require(zlib.crc32(checked) == checksum, 'Не совпала контрольная сумма заголовка GPT.')
    current, alternate, first, last = struct.unpack_from('<QQQQ', raw, 24)
    require(current == lba and 0 < alternate < sectors and 34 <= first <= last < sectors - 1, 'Неверные границы GPT.')
    table, count, entry_size, entries_crc = struct.unpack_from('<QIII', raw, 72)
    require(entry_size == 128 and 1 <= count <= 4096 and table > 1 and table * 512 + count * entry_size <= sectors * 512, 'Недопустимая таблица GPT.')
    table_sectors = (count * entry_size + 511) // 512
    require(table + table_sectors <= first if lba == 1 else
            last < table and table + table_sectors <= lba,
            'Метаданные GPT пересекают доступные секторы или резервный заголовок.')
    entries = read(table * 512, table_sectors * 512)[:count * entry_size]
    require(zlib.crc32(entries) == entries_crc, 'Не совпала контрольная сумма таблицы GPT.')
    return raw[56:72], alternate, entries, (first, last)


def partitions(read, disk_bytes):
    mbr = read(0, 512)
    require(mbr[510:] == b'\x55\xaa', 'Не найдена подпись загрузочного сектора.')
    is_gpt = mbr[450] == 0xEE
    if is_gpt:
        sectors = disk_bytes // 512
        first, count = struct.unpack_from('<II', mbr, 454)
        require(mbr[446] == 0 and first == 1 and count == min(sectors - 1, 0xFFFFFFFF) and mbr[462:510] == b'\0' * 48,
                'Повреждена защитная запись MBR для GPT.')
        guid, alternate, entries, usable = gpt_header(read, 1, sectors)
        backup_guid, back_alternate, backup_entries, backup_usable = gpt_header(read, sectors - 1, sectors)
        require(alternate == sectors - 1 and back_alternate == 1 and guid == backup_guid
                and entries == backup_entries and usable == backup_usable,
                'Основная и резервная таблицы GPT не совпали.')
        result = []
        basic_data = uuid.UUID('ebd0a0a2-b9e5-4433-87c0-68b6b72699c7').bytes_le
        for offset in range(0, len(entries), 128):
            entry = entries[offset:offset + 128]
            if entry[:16] != b'\0' * 16:
                require(entry[:16] == basic_data, 'Не совпал тип раздела FlashOS в GPT.')
                if len(result) == 1:
                    flags = struct.unpack_from('<Q', entry, 48)[0]
                    require(flags & 0x8000000000000000, 'Отсутствует защитный атрибут раздела Ventoy в GPT.')
                first, last = struct.unpack_from('<QQ', entry, 32)
                require(last >= first, 'Неверный размер раздела GPT.')
                require(usable[0] <= first <= last <= usable[1],
                        'Раздел выходит за границы доступных секторов GPT.')
                result.append((first * 512, (last - first + 1) * 512))
    else:
        require([mbr[446 + i * 16] for i in range(3)] == [0x80, 0, 0] and
                [mbr[450 + i * 16] for i in range(3)] == [0x07, 0xEF, 0x0C] and mbr[494:510] == b'\0' * 16,
                'Не совпали типы или признаки разделов MBR.')
        result = []
        for index in range(4):
            offset = 446 + index * 16
            if mbr[offset + 4]:
                first, count = struct.unpack_from('<II', mbr, offset + 8)
                result.append((first * 512, count * 512))
        require(mbr[446] == 0x80 and mbr[466] == 0xEF, 'Не совпали признаки разделов Ventoy в MBR.')
    require(len(result) == 3, 'Ожидались три раздела FlashOS.')
    require(result[0][0] == 1024**2 and result[1][1] == 32 * 1024**2 and result[2][1] >= 250 * 1024**2,
            'Разметка не соответствует FlashOS.')
    for index, (offset, size) in enumerate(result):
        require(size > 0 and offset + size <= disk_bytes, 'Раздел выходит за пределы устройства.')
        if index:
            gap = offset - sum(result[index - 1])
            require(gap == 0 if index == 1 else 0 <= gap < 1024**2, 'Неверное расположение разделов FlashOS.')
    return mbr, is_gpt, result


def fat16_files(data):
    """Read a bounded FAT16 volume, including validated long filenames."""
    sector = struct.unpack_from('<H', data, 11)[0]
    cluster_sectors = data[13]
    reserved = struct.unpack_from('<H', data, 14)[0]
    fats, root_entries = data[16], struct.unpack_from('<H', data, 17)[0]
    fat_sectors = struct.unpack_from('<H', data, 22)[0]
    require(sector == 512 and cluster_sectors in (1, 2, 4, 8, 16, 32, 64, 128) and fats in (1, 2) and reserved and fat_sectors and root_entries,
            'Повреждён формат раздела VTOYEFI.')
    fat_start = reserved * sector
    fat = data[fat_start:fat_start + fat_sectors * sector]
    if fats == 2:
        require(fat[4:] == data[fat_start + len(fat) + 4:fat_start + 2 * len(fat)], 'Копии FAT раздела VTOYEFI не совпадают.')
    root_start = (reserved + fats * fat_sectors) * sector
    data_start = root_start + ((root_entries * 32 + sector - 1) // sector) * sector
    cluster_bytes = cluster_sectors * sector
    max_cluster = (len(data) - data_start) // cluster_bytes + 1
    require(4085 <= max_cluster < 65525, 'Ожидалась файловая система FAT16.')
    files, seen_directories = {}, set()

    def chain(start):
        seen, blocks = set(), []
        while start < 0xFFF8:
            require(2 <= start <= max_cluster and start not in seen and start * 2 + 2 <= len(fat), 'Повреждена цепочка кластеров VTOYEFI.')
            seen.add(start)
            offset = data_start + (start - 2) * cluster_bytes
            blocks.append(data[offset:offset + cluster_bytes])
            start = struct.unpack_from('<H', fat, start * 2)[0]
        return b''.join(blocks)

    def directory(raw, prefix, depth):
        require(depth < 20 and len(files) < 20000, 'Слишком сложная структура VTOYEFI.')
        long_parts, long_checksum = {}, None
        for offset in range(0, len(raw) - 31, 32):
            item = raw[offset:offset + 32]
            if item[0] == 0:
                break
            if item[0] == 0xE5:
                long_parts = {}
                continue
            if item[11] == 0x0F:
                order = item[0] & 31
                if item[0] & 0x40:
                    long_parts = {}
                long_parts[order] = item[1:11] + item[14:26] + item[28:32]
                long_checksum = item[13]
                continue
            if item[11] & 8:
                long_parts = {}
                continue
            short = item[:8].decode('cp437').rstrip()
            ext = item[8:11].decode('cp437').rstrip()
            name = short + ('.' + ext if ext else '')
            if long_parts:
                checksum = 0
                for byte in item[:11]:
                    checksum = (((checksum & 1) << 7) + (checksum >> 1) + byte) & 255
                require(checksum == long_checksum and set(long_parts) == set(range(1, len(long_parts) + 1)), 'Повреждено длинное имя файла VTOYEFI.')
                name = b''.join(long_parts[i] for i in sorted(long_parts)).decode('utf-16-le').split('\0', 1)[0].rstrip('\uffff')
            long_parts = {}
            if name in ('.', '..'):
                continue
            require(name and '/' not in name and '\\' not in name, 'Недопустимое имя файла VTOYEFI.')
            key = (prefix + name).casefold()
            cluster = struct.unpack_from('<H', item, 26)[0]
            size = struct.unpack_from('<I', item, 28)[0]
            if item[11] & 16:
                require(cluster not in seen_directories, 'Зацикленная папка VTOYEFI.')
                seen_directories.add(cluster)
                directory(chain(cluster), key + '/', depth + 1)
            else:
                content = chain(cluster) if size else b''
                require(len(content) >= size and key not in files, 'Повреждён файл VTOYEFI.')
                files[key] = (hashlib.sha256(content[:size]).hexdigest(), item[11] & 7)
    directory(data[root_start:root_start + root_entries * 32], '', 0)
    return files


def verify_boot(lease, work, manifest, emit, cancelled, expected_style=None):
    emit(dict(kind='phase', phase='boot', message='Проверка таблицы разделов, BIOS и файлов загрузчика UEFI', percent=None))
    def read(offset, length):
        if cancelled():
            raise InterruptedError('Проверка загрузчика остановлена.')
        lease.check()
        return lease.read(offset, length)
    def source(path):
        record = next(x for x in manifest['files'] if x['section'] == 'engine' and x['path'] == path)
        return (Path(work) / 'objects' / record['sha256']).read_bytes()
    actual, gpt, parts = partitions(read, lease.target['size'])
    if expected_style is not None:
        require(gpt == (expected_style == 'GPT'), 'Тип созданной разметки не совпал с выбранным.')
    expected = bytearray(source('boot/boot.img'))
    require(actual[384:388] == actual[440:444], 'Не совпали две копии идентификатора загрузочного диска.')
    expected[384:400] = actual[384:400]
    expected[440:444] = actual[440:444]
    expected[446:510] = actual[446:510]
    if gpt:
        expected[92] = 0x22
    require(actual == expected, 'Не совпал загрузочный код MBR.')
    core = bytearray(lzma.decompress(source('boot/core.img.xz')))
    if gpt:
        core[500] = 0x23
        core = core[:2014 * 512]
    require(read((34 if gpt else 1) * 512, len(core)) == core, 'Не совпал загрузочный код BIOS.')
    expected_efi = lzma.decompress(source('ventoy/ventoy.disk.img.xz'))
    actual_efi = read(parts[1][0], parts[1][1])
    if actual_efi != expected_efi:
        original_sector, written_sector = bytearray(expected_efi[:512]), bytearray(actual_efi[:512])
        # FAT16 state byte: allow a mounted/dirty flag, preserve every BPB/boot-code byte.
        original_sector[37] &= ~1
        written_sector[37] &= ~1
        require(original_sector == written_sector, 'Повреждён загрузочный сектор VTOYEFI.')
        fat_offset = struct.unpack_from('<H', expected_efi, 14)[0] * 512
        require(actual_efi[fat_offset:fat_offset + 2] == expected_efi[fat_offset:fat_offset + 2], 'Повреждён идентификатор FAT.')
        expected_flags = struct.unpack_from('<H', expected_efi, fat_offset + 2)[0]
        actual_flags = struct.unpack_from('<H', actual_efi, fat_offset + 2)[0]
        require((expected_flags & 0x7FFF) == (actual_flags & 0x7FFF), 'В FAT отмечены ошибки ввода-вывода.')
        # Windows may add filesystem bookkeeping. Every shipped file must still match.
        original, written = fat16_files(expected_efi), fat16_files(actual_efi)
        different = [name for name, digest in original.items() if written.get(name) != digest]
        require(not different, 'Повреждён загрузчик UEFI: ' + ', '.join(different[:3]))
        unexpected = [name for name in written if name not in original and not name.startswith(('system volume information/', '$recycle.bin/'))]
        require(not unexpected, 'Неизвестные файлы на служебном разделе UEFI: ' + ', '.join(unexpected[:3]))
    emit(dict(kind='log', phase='boot', message='Загрузочный код BIOS, таблица разделов и все исходные файлы VTOYEFI совпали.'))

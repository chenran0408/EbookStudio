"""Rebuild the byte-identical EbookStudio release ZIP using only Python modules from the standard library.

Use CPython 3.14.0 Windows x64, or Linux Python linked to the same zlib-ng 2.2.4
in zlib-compatible mode. On Linux a compatible libz.so can be selected with LD_PRELOAD.
"""
import hashlib
import json
import struct
import sys
import zipfile
import zlib
from functools import lru_cache
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def compressed_region(fp, item):
    fp.seek(item.header_offset + 26)
    n, m = struct.unpack('<HH', fp.read(4))
    return item.header_offset + 30 + n + m


def deflate(data):
    obj = zlib.compressobj(6, zlib.DEFLATED, -15)
    return b''.join(obj.compress(data[i:i + 8192]) for i in range(0, len(data), 8192)) + obj.flush()


def restore(old, patch, output):
    with zipfile.ZipFile(patch) as package:
        recipe = json.loads(package.read('recipe.json'))
        pool = package.read('data')
    with open(old, 'rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != recipe['base']:
            raise ValueError('Base ZIP SHA-256 differs')
    with zipfile.ZipFile(old) as oldzip, open(old, 'rb') as fp, open(output, 'xb') as out:
        items = oldzip.infolist()
        @lru_cache(maxsize=8)
        def source(index):
            return oldzip.read(items[index])
        aggregate = hashlib.sha256()
        def write(data):
            aggregate.update(data)
            out.write(data)
        def literal(ref):
            offset, length = ref
            return pool[offset:offset + length]
        for hdr, mode, arg, checksum in recipe['entries']:
            write(literal(hdr))
            if mode == 'copy':
                fp.seek(compressed_region(fp, items[arg]))
                result = fp.read(items[arg].compress_size)
            elif mode == 'literal':
                result = literal(arg)
            else:
                if mode == 'old':
                    raw = source(arg)
                elif mode == 'build':
                    raw = b''.join(literal(op[1:]) if op[0] == -1 else source(op[0])[op[1]:op[1] + op[2]] for op in arg)
                else:
                    raise ValueError(f'Unknown operation {mode}')
                result = deflate(raw)
                if digest(result) != checksum:
                    raise ValueError('Different compressed bytes produced. Select the packaging compressor zlib-ng 2.2.4 or run on Windows x64 CPython 3.14.0.')
            write(result)
        write(literal(recipe['tail']))
        if aggregate.hexdigest() != recipe['target']:
            raise ValueError('Reconstructed ZIP SHA-256 differs')
        print(aggregate.hexdigest(), Path(output).name)


if __name__ == '__main__':
    restore(*sys.argv[1:])

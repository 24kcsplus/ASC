"""Operations run only in the isolated analysis process (no Qt or MCP imports)."""
from dataclasses import asdict
from pathlib import Path
import re
import uuid
from src.asc_client.gui.runtime import GuiDexStore
from asc_gui.definitions import DexScope, find_definitions
from asc_gui.navigation import ClassLookup, navigation_query, type_label
from asc_gui.references import exact_references
from asc_gui.variables import find_variable_definition


def page(items, offset, limit, total=None):
    available = len(items)
    total = available if total is None else total
    end = min(offset + limit, available)
    return {'items': items[offset:end], 'offset': offset, 'total': total,
            'available': available, 'next_offset': end if end < available else None,
            'truncated': available < total}


def text_page(source, offset, limit):
    end = min(offset + limit, len(source))
    return {'text': source[offset:end], 'offset': offset, 'total_chars': len(source),
            'start_line': source.count('\n', 0, min(offset, len(source)))+1,
            'next_offset': end if end < len(source) else None}


class AnalysisSession:
    def __init__(self):
        self.store = None
        self.session_id = None
        self.manifest = None

    def status(self):
        return {'loaded': self.store is not None, 'session_id': self.session_id,
                'apk_path': self.store.apk_path if self.store else None,
                'class_count': len(self.store.class_names) if self.store else 0,
                'dex_count': len(self.store.dex_buffers) if self.store else 0}

    def class_key(self, name):
        name = name.strip()
        if re.fullmatch(r'L[^;\s]+;', name):
            return name
        if not name or any(c in name for c in ';[ \t\n'):
            raise ValueError('请提供完整类名或 DEX 类描述符')
        return 'L'+name.replace('.', '/')+';'

    def source(self, key):
        dex, source = self.store.get_source(key)
        while len(self.store.source_cache) > 32:
            self.store.source_cache.pop(next(iter(self.store.source_cache)))
        return dex, source

    def call(self, op, args):
        if op == 'status':
            return self.status()
        if op == 'open':
            path = Path(args['path']).expanduser().resolve(strict=True)
            if not path.is_file() or path.suffix.lower() != '.apk':
                raise ValueError('path 必须是本地 APK 文件')
            candidate = GuiDexStore(str(path), max_workers=4)
            candidate.load()
            if not candidate.class_names:
                raise ValueError('APK 中没有可分析的 DEX 类')
            self.store = candidate
            self.session_id = uuid.uuid4().hex
            self.manifest = None
            return self.status()
        if op == 'close':
            self.store = self.manifest = self.session_id = None
            return self.status()
        if self.store is None:
            raise ValueError('请先调用 asc_open_apk；进程超时或取消后也需要重新打开 APK')
        result = self._query(op, args)
        return {**result, 'session_id': self.session_id, 'apk_path': self.store.apk_path}

    def _query(self, op, args):
        store = self.store
        offset, limit = args.get('offset', 0), args.get('limit', 100)
        if op == 'classes':
            query = args.get('query', '').casefold()
            items = [key for key in store.class_names if query in key.casefold() or query in type_label(key).casefold()]
            return page(items, offset, limit)
        if op == 'manifest':
            if self.manifest is None:
                from src.asc_client.manifest_handler import get_manifest_xml
                self.manifest = get_manifest_xml(store.apk_path)
            return text_page(self.manifest, offset, limit)
        if op == 'search_members':
            found = store.search_members(args['kind'], args['query'])
            return {**page(found['results'], offset, limit, found['total_hits']),
                    'match_mode': 'global_name_contains'}
        key = self.class_key(args['class_name'])
        if op == 'source':
            dex, source = self.source(key)
            return {**text_page(source, offset, limit), 'class_name': key, 'dex_name': dex}
        if op == 'members':
            clazz = DexScope(store).get_class(key)
            if clazz is None:
                raise ValueError('APK 内没有此类的定义：'+key)
            items = []
            if args['kind'] in ('all', 'method'):
                for method in clazz.methods:
                    prototype = method.prototype
                    items.append({'kind': 'method', 'name': method.name,
                        'parameters': [p.descriptor for p in prototype.parameters_type],
                        'return_type': clazz.dex.get_type(prototype.return_type_idx).descriptor,
                        'access_flags': method.access_flags})
            if args['kind'] in ('all', 'field'):
                items.extend({'kind': 'field', 'name': f.name, 'type': f.type.descriptor,
                              'access_flags': f.access_flags, 'static': f.is_static} for f in clazz.fields)
            return {**page(items, offset, limit), 'class_name': key,
                    'parents': list(DexScope(store).parents(clazz))}
        if op == 'definitions':
            found = find_definitions(store, {'owners': [key], 'name': args['method_name'], 'arity': args.get('arity')})
            return {**page(found['candidates'], offset, limit, found['total']),
                    'owners': found['owners'], 'reason': found['reason']}
        if op == 'references':
            kind = 'type' if args['kind'] == 'class' else args['kind']
            if kind != 'type' and not args['name']:
                raise ValueError('方法或字段引用必须提供 name')
            found = exact_references(store, {'kind': kind, 'name': key if kind == 'type' else args['name'],
                                             'owners': [key], 'arity': args.get('arity')})
            return {**page(found['results'], offset, limit, found['total_hits']),
                    'owners': found['owners'], 'match_mode': 'exact_owner_and_name',
                    'reason': found.get('reason', '')}
        if op == 'resolve':
            _, source = self.source(key)
            lines = source.splitlines(keepends=True)
            line, column = args['line'], args['column']
            if line > len(lines) or column > len(lines[line-1].rstrip('\r\n')):
                raise ValueError('行或列超出源码范围；均从 1 开始，列按 Unicode 字符计数')
            position = sum(map(len, lines[:line-1])) + column-1
            variable = find_variable_definition(source, position, key)
            if variable:
                return {'kind': variable.kind, 'target': asdict(variable),
                        'declaration_line': source.count('\n', 0, variable.start)+1, 'class_name': key}
            query = navigation_query(source, position, key, ClassLookup(store.class_names))
            if query is None:
                return {'kind': None, 'reason': '此位置没有可解析的类、方法或局部变量'}
            if query.kind == 'class':
                return {'kind': 'class', 'owners': query.owners, 'uncertain': query.uncertain}
            found = find_definitions(store, asdict(query))
            return {'kind': 'method', 'query': asdict(query),
                    **page(found['candidates'], 0, 100, found['total']), 'reason': found['reason'], 'owners': found['owners']}
        raise ValueError('未知分析操作：'+op)

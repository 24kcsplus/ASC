"""Locate actual DEX method definitions, retaining overload signatures."""
import struct
from .navigation import type_label


class DexScope:
    def __init__(self, store):
        self.store = store

    def get_class(self, key):
        dex_name = self.store.class_to_dex.get(key)
        if dex_name is None:
            return None
        return self.store.findref_managers[dex_name].dex.get_class(key)

    def parents(self, clazz):
        dex = clazz.dex
        super_idx = struct.unpack_from('<I', dex.buf, clazz._class_def_off+8)[0]
        if super_idx != 0xffffffff:
            yield dex.get_type(super_idx).descriptor
        interface_off = struct.unpack_from('<I', dex.buf, clazz._class_def_off+12)[0]
        if interface_off:
            count = struct.unpack_from('<I', dex.buf, interface_off)[0]
            for index in range(count):
                type_idx = struct.unpack_from('<H', dex.buf, interface_off+4+index*2)[0]
                yield dex.get_type(type_idx).descriptor

    def field_types(self, keys, field_name):
        visited = set()
        while keys:
            found, following = set(), []
            for key in keys:
                if key in visited:
                    continue
                visited.add(key)
                clazz = self.get_class(key)
                if clazz is None:
                    continue
                found.update(f.type.descriptor for f in clazz.fields if f.name == field_name)
                following.extend(self.parents(clazz))
            if found:
                return sorted(found)
            keys = following
        return []

    def follow_fields(self, keys, path):
        for field_name in path:
            keys = self.field_types(keys, field_name)
            if not keys:
                break
        return keys

    def receiver_owners(self, request):
        owners = request.get('owners', [])
        path = request.get('fields', [])
        if request.get('super_only'):
            owners = [p for key in owners if (clazz := self.get_class(key)) for p in self.parents(clazz)]
        implicit = request.get('implicit_fields', [])
        root_types = self.field_types([request.get('current_class', '')], implicit[0]) if implicit else []
        if root_types:
            # An existing field wins even if its type is external or a later
            # chain segment cannot be resolved; never fall back to a class alias.
            owners = self.follow_fields(root_types, implicit[1:])
        else:
            owners = self.follow_fields(owners, path)
        return owners

    def declaring_owners(self, owners, name, kind):
        seen = set()
        frontier = list(owners)
        while frontier:
            found, following = set(), []
            for key in frontier:
                if key in seen:
                    continue
                seen.add(key)
                clazz = self.get_class(key)
                if clazz is None:
                    continue
                members = clazz.methods if kind == 'method' else clazz.fields
                if any(member.name == name for member in members):
                    found.add(key)
                else:
                    following.extend(self.parents(clazz))
            if found:
                return sorted(found)
            frontier = following
        return []


def find_definitions(store, request):
    name = request['name']
    arity = request.get('arity')
    owners = request.get('owners', [])
    seen, candidates = set(), []
    total = 0

    scope = DexScope(store)
    get_class, parents = scope.get_class, scope.parents
    allow_global = request.get('allow_global', False)
    reason = request.get('reason', '')
    if not allow_global:
        owners = scope.receiver_owners(request)
        if not owners:
            return {'candidates': [], 'total': 0, 'uncertain': True, 'owners': [],
                    'reason': reason or '无法确定调用所属类；可右键选择「全局查找同名方法定义」'}

    def inspect(key, clazz=None):
        nonlocal total
        clazz = clazz or get_class(key)
        if clazz is None:
            return []
        matches = []
        for method in clazz.methods:
            if method.name != name:
                continue
            params = [type_label(p.descriptor) for p in method.prototype.parameters_type]
            # Varargs calls may have a different source argument count.
            if arity is not None and len(params) != arity and not method.access_flags & 0x80:
                continue
            matches.append({'class_name': key, 'name': name, 'params': params,
                            'signature': key[1:-1].replace('/', '.')+' → '+name+'('+', '.join(params)+')'})
        total += len(matches)
        return matches

    if not allow_global:
        frontier = list(owners)
        while frontier:
            following = []
            for key in frontier:
                if key in seen:
                    continue
                seen.add(key)
                matches = inspect(key)
                candidates.extend(matches)
                if not matches and (clazz := get_class(key)):
                    following.extend(parents(clazz))
            if candidates:
                break
            frontier = following
    else:
        for manager in store.findref_managers.values():
            dex = manager.dex
            for index in range(len(dex.classes)):
                clazz = dex.classes[index]
                if clazz.fullname in seen:
                    continue
                seen.add(clazz.fullname)
                matches = inspect(clazz.fullname, clazz)
                if len(candidates) < 300:
                    candidates.extend(matches[:300-len(candidates)])
    if not candidates and not allow_global:
        reason = '在 '+', '.join(type_label(key) for key in owners)+' 及其继承层次中没有可用定义（可能属于外部依赖）'
    return {'candidates': candidates, 'total': total, 'owners': owners, 'reason': reason,
            'global_search': allow_global, 'uncertain': allow_global or len(owners) != 1}

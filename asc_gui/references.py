"""Exact DEX owner/member reference queries, using ASC's instruction scanner."""
from collections import defaultdict
from .definitions import DexScope
from .navigation import type_label


def exact_references(store, request, progress=None):
    kind, name = request['kind'], request['name']
    scope = DexScope(store)
    owners = request.get('owners', []) if kind == 'type' else scope.receiver_owners(request)
    result = {'results': [], 'total_hits': 0, 'owners': owners, 'exact': True}
    if len(owners) != 1:
        result['reason'] = request.get('reason') or '无法唯一确定所属类；请手动指定类后搜索引用'
        return result
    if kind != 'type':
        declared = scope.declaring_owners(owners, name, kind)
        # Keep the receiver ID too: DEX may encode an inherited access using it.
        owners = sorted(set(owners + declared))
    result['owners'] = owners
    result['scope_text'] = ', '.join(type_label(key) for key in owners)
    arity = request.get('arity') if kind == 'method' else None
    # Account for varargs definitions before applying the source argument count.
    varargs = kind == 'method' and any(
        m.name == name and m.access_flags & 0x80
        for key in owners if (clazz := scope.get_class(key)) for m in clazz.methods)
    managers = store.findref_managers
    for done, (dex_name, manager) in enumerate(managers.items(), 1):
        dex = manager.dex
        grouped = defaultdict(set)
        matched_owners = defaultdict(set)
        # A class query includes direct type instructions and access to its
        # methods/fields, including calls through instances in other classes.
        for scan_kind in ('type', 'method', 'field') if kind == 'type' else (kind,):
            if scan_kind == 'type':
                table = dex.types
                indexes = {i for i in manager._get_type_locator(True).locate(owners[0])
                           if table[i].descriptor == owners[0]}
            else:
                table = dex.methods if scan_kind == 'method' else dex.fields
                locator = manager._get_method_locator(True) if scan_kind == 'method' else manager._get_field_locator(True)
                indexes = set()
                member_name = None if kind == 'type' else name
                for owner in owners:
                    indexes.update(i for i in locator.locate({'class': [owner, True], scan_kind: member_name})
                                   if (member_name is None or table[i].name == member_name) and table[i].cls.fullname == owner
                                   and (arity is None or varargs or len(table[i].prototype.parameters_type) == arity))
            if not indexes:
                continue
            # Definition-only DEX files have no code_item region to scan.
            if not any(m.code_offset for i in range(len(dex.classes)) for m in dex.classes[i].methods):
                continue
            scanner = manager._get_code_scanner()
            query = {scan_kind: indexes}
            scanner.scan(query, True)
            for caller, matched in zip(query[scan_kind], scanner.mark_idx):
                if caller is None:
                    continue
                member = table[matched]
                owner = member.descriptor if scan_kind == 'type' else member.cls.fullname
                label = owner if scan_kind == 'type' else owner+'->'+member.name
                if scan_kind == 'method':
                    label += '('+', '.join(type_label(p.descriptor) for p in member.prototype.parameters_type)+')'
                for mid in caller if isinstance(caller, list) else [caller]:
                    grouped[mid].add(label)
                    matched_owners[mid].add(owner)
        for mid, matches in sorted(grouped.items()):
            caller = dex.methods[mid]
            result['total_hits'] += 1
            if len(result['results']) < 5000:
                method_text = caller.cls.fullname+'->'+caller.name
                matched_text = '; '.join(sorted(matches))
                result['results'].append({'dex_name': dex_name, 'class_name': caller.cls.fullname,
                    'class_display': type_label(caller.cls.fullname), 'method_text': method_text,
                    'matched_text': matched_text, 'matched_owners': sorted(matched_owners[mid]),
                    'line': dex_name+' | '+method_text+' | matched=('+matched_text+')'})
        if progress:
            progress(done, len(managers), result['total_hits'])
    return result

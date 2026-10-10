"""Source navigation hints and declaration locations; no Java execution."""
from dataclasses import dataclass, field
import re
from .xref import IDENT, KEYWORDS, TOKENS


def mask_literals(source, preserve_values=False):
    parts, end = [], 0
    for match in TOKENS.finditer(source):
        if match.lastgroup in ('comment', 'string', 'char'):
            masked = re.sub(r'[^\n]', ' ', match.group())
            if preserve_values and match.lastgroup != 'comment':
                masked = '0' + masked[1:]
            parts.extend((source[end:match.start()], masked))
            end = match.end()
    parts.append(source[end:])
    return ''.join(parts)


def identifier_at(source, offset):
    for token in TOKENS.finditer(source):
        if token.start() <= offset < token.end():
            if token.lastgroup == 'ident' and token.group() not in KEYWORDS:
                return token.start(), token.end(), token.group()
            return None
        if token.start() > offset:
            break
    return None


def closing_paren(text, start):
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] == '(':
            depth += 1
        elif text[pos] == ')':
            depth -= 1
            if depth == 0:
                return pos
    return None


def split_parameters(text):
    if not text.strip():
        return []
    parts, start, depth = [], 0, 0
    for pos, char in enumerate(text):
        if char in '(<[{':
            depth += 1
        elif char in ')>]}':
            depth = max(0, depth - 1)
        elif char == ',' and not depth:
            parts.append(text[start:pos].strip())
            start = pos + 1
    parts.append(text[start:].strip())
    return parts


MODIFIERS = r'(?:public|private|protected|static|final|synchronized|native|abstract|default|synthetic|bridge|strictfp)'
DECLARATION = re.compile(r'(?m)^[ \t]*(?P<prefix>(?:' + MODIFIERS + r'\s+)*(?:[\w.$<>\[\],?]+[ \t]+)?)(?P<name>' + IDENT + r')\s*\(')


def declarations(source, class_name=''):
    clean = mask_literals(source)
    rows = []
    simple_class = class_name.rsplit('/', 1)[-1].rstrip(';').rsplit('$', 1)[-1]
    for match in DECLARATION.finditer(clean):
        name, prefix = match['name'], match['prefix'].strip()
        if name in KEYWORDS or not prefix or prefix.split()[0] in ('return', 'new', 'throw'):
            continue
        start = clean.find('(', match.start(), match.end())
        end = closing_paren(clean, start)
        if end is None:
            continue
        tail = re.match(r'\s*(?:throws\s+[\w.$, \t\n]+)?\s*([;{])', clean[end+1:])
        if not tail:
            continue
        if tail[1] == ';' and not re.search(r'\b(?:abstract|native)\b', prefix) and 'interface ' not in clean[:match.start()]:
            continue
        args = split_parameters(clean[start+1:end])
        types = []
        for arg in args:
            arg = re.sub(r'\bfinal\s+', '', arg)
            typ = re.sub(r'\s+' + IDENT + r'(\[\])?\s*$', lambda m: m[1] or '', arg).strip()
            types.append(typ)
        rows.append({'name': name, 'line': clean.count('\n', 0, match.start('name'))+1,
                     'offset': match.start('name'), 'params': types, 'arity': len(args),
                     'signature': name+'('+', '.join(types)+')'})
    return rows


class ClassLookup:
    def __init__(self, classes):
        self.classes = set(classes)
        self.aliases = {}
        for key in classes:
            dotted = key[1:-1].replace('/', '.')
            for alias in {dotted, dotted.replace('$', '.'), dotted.rsplit('.', 1)[-1], dotted.rsplit('$', 1)[-1]}:
                self.aliases.setdefault(alias, set()).add(key)

    def resolve(self, name, source='', current=''):
        name = re.sub(r'<.*?>|\[\]|\.\.\.', '', name).strip()
        if name in self.classes:
            return [name]
        candidates = self.aliases.get(name, set())
        if '.' in name:
            return sorted(candidates)
        if current and current in candidates:
            return [current]
        imported = re.findall(r'\bimport\s+([\w.$]+)\s*;', mask_literals(source))
        for full in imported:
            if full.rsplit('.', 1)[-1] == name:
                return sorted(self.aliases.get(full, set()))
        package = current[1:-1].rsplit('/', 1)[0].replace('/', '.') if '/' in current else ''
        same_package = self.aliases.get(package+'.'+name, set())
        if same_package:
            return sorted(same_package)
        if current:
            nested = current[:-1]+'$'+name+';'
            if nested in self.classes:
                return [nested]
        return sorted(candidates)


    def resolve_scoped(self, name, source='', current=''):
        """Resolve a receiver type without collecting global simple-name matches."""
        name = re.sub(r'<.*>', '', name).strip()
        if '[' in name or '...' in name:
            return []
        if name in self.classes:
            return [name]
        if '.' in name:
            exact = sorted(self.aliases.get(name, set()))
            return exact or ['L'+name.replace('.', '/')+';']
        if current and current in self.aliases.get(name, set()):
            return [current]
        if current and current[:-1]+'$'+name+';' in self.classes:
            return [current[:-1]+'$'+name+';']
        imports = re.findall(r'\bimport\s+([\w.$]+)\s*;', mask_literals(source))
        for full in imports:
            if full.rsplit('.', 1)[-1] == name:
                return self.resolve_scoped(full)
        package = current[1:-1].rsplit('/', 1)[0]+'/' if '/' in current else ''
        key = 'L'+package+name+';'
        if key in self.classes:
            return [key]
        wildcards = re.findall(r'\bimport\s+([\w.$]+)\.\*\s*;', mask_literals(source))
        return sorted({key for package in wildcards for key in self.aliases.get(package+'.'+name, set())})


@dataclass
class NavigationQuery:
    name: str
    kind: str
    owners: list
    arity: int | None = None
    uncertain: bool = False
    super_only: bool = False
    fields: list = field(default_factory=list)
    implicit_fields: list = field(default_factory=list)
    current_class: str = ''
    receiver: str = ''
    reason: str = ''


def navigation_query(source, offset, current, lookup, member_kind=None):
    token = identifier_at(source, offset)
    if token is None:
        return None
    start, end, name = token
    clean = mask_literals(source)
    before, after = clean[:start], clean[end:]
    qualifier = re.search(r'(?:'+IDENT+r'\s*\.\s*)+$', before)
    qualified = re.sub(r'\s+', '', qualifier.group())+name if qualifier else name
    type_candidates = lookup.resolve(qualified, source, current)
    is_call = re.match(r'\s*\(', after)
    constructor = re.search(r'\bnew\s+(?:'+IDENT+r'\s*\.\s*)*$', before)
    if type_candidates and member_kind is None and (not is_call or constructor):
        return NavigationQuery(name, 'class', type_candidates, uncertain=len(type_candidates)>1)
    # A call named like an obfuscated class is still a method call, unless new precedes it.
    if not is_call and member_kind != "field":
        return None
    opening = end + is_call.end()-1 if is_call else end
    closing = closing_paren(clean, opening) if is_call else None
    # Counting generic invocation arguments is ambiguous; do not narrow in that case.
    arg_text = mask_literals(source[opening+1:closing], preserve_values=True) if closing is not None else None
    arity = len(split_parameters(arg_text)) if arg_text is not None and '<' not in arg_text else None
    receiver = re.sub(r'\s+', '', qualifier.group()).rstrip('.') if qualifier else ''
    query = NavigationQuery(name, member_kind or 'method', [], arity, current_class=current, receiver=receiver or 'this')
    # A trailing expression (factory().a(), items[0].a(), casts) is not this.a().
    prefix = before[:qualifier.start()] if qualifier else before
    if prefix.rstrip().endswith('.'):
        query.uncertain = True
        query.reason = '暂不能确定复杂接收者表达式的类型'
        return query
    parts = receiver.split('.') if receiver else ['this']
    if parts[0] in ('this', 'super'):
        query.owners = [current]
        query.fields = parts[1:]
        query.super_only = parts[0] == 'super'
        return query
    # Reuse lexical scope resolution: variables shadow fields and class names.
    from .variables import find_variable_definition
    variable = find_variable_definition(source, qualifier.start(), current) if qualifier else None
    if variable is not None:
        query.owners = lookup.resolve_scoped(variable.type_name, source, current)
        query.fields = parts[1:]
        query.uncertain = len(query.owners) != 1
        if not query.owners:
            query.reason = '无法确定变量 '+parts[0]+' 的可用类类型（声明为 '+variable.type_name+'）'
        return query
    # The worker checks real fields (including inherited fields) before treating
    # an unqualified receiver as a static class. A field named a shadows class a.
    query.implicit_fields = parts
    for count in range(len(parts), 0, -1):
        type_name = '.'.join(parts[:count])
        owners = lookup.resolve_scoped(type_name, source, current)
        # Do not invent a package/class boundary for an arbitrary field chain.
        known = [key for key in owners if key in lookup.classes]
        if known or (count == 1 and owners):
            query.owners = owners
            query.fields = parts[count:]
            break
    query.uncertain = len(query.owners) != 1
    return query


def type_label(descriptor):
    dimensions = len(descriptor) - len(descriptor.lstrip('['))
    value = descriptor[dimensions:]
    primitive = {'V':'void','Z':'boolean','B':'byte','S':'short','C':'char','I':'int','J':'long','F':'float','D':'double'}
    value = primitive.get(value, value[1:-1].replace('/', '.') if value.startswith('L') else value)
    return value+'[]'*dimensions


def matching_declarations(source, candidate):
    name = candidate['name']
    if name == '<init>':
        name = candidate['class_name'].rsplit('/',1)[-1].rstrip(';').rsplit('$',1)[-1]
    rows = [row for row in declarations(source, candidate['class_name']) if row['name'] == name]
    if 'params' not in candidate:
        return rows
    rows = [row for row in rows if row['arity'] == len(candidate['params'])]
    def normalized(value):
        return re.sub(r'<.*>', '', value).replace('...', '[]').rsplit('.', 1)[-1].rsplit('$', 1)[-1]
    exact = [row for row in rows if [normalized(x) for x in row['params']] == [normalized(x) for x in candidate['params']]]
    return exact  # Never jump to an arbitrary same-arity overload.

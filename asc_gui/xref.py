"""Source reference hints; receiver ownership is resolved by navigation/DEX."""
from dataclasses import dataclass
import re

IDENT = r'(?:[^\W\d]|[$])(?:\w|[$])*'
TOKENS = re.compile(
    r'(?P<comment>//[^\n]*|/\*[\s\S]*?(?:\*/|\Z))'
    r'|(?P<string>"(?:\\.|[^"\\])*")'
    r"|(?P<char>'(?:\\.|[^'\\])*')"
    r'|(?P<ident>' + IDENT + r')')
KEYWORDS = set('abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for if implements import instanceof int interface long native new package private protected public return short static super switch synchronized this throw throws transient try void volatile while true false null'.split())


@dataclass(frozen=True)
class ReferenceTarget:
    query: str
    kind: str | None


def python_offset(text, utf16_offset):
    """Translate QTextCursor UTF-16 offsets without shifting after emoji."""
    return len(text.encode('utf-16-le')[:utf16_offset * 2].decode('utf-16-le', errors='ignore'))


def infer_reference(text, offset, selection_end=None, classes=()):
    if not text:
        return None
    offset = max(0, min(offset, len(text)))
    selected = text[offset:selection_end].strip() if selection_end is not None else ''
    if '\n' in selected or len(selected) > 500:
        return None
    token = None
    for match in TOKENS.finditer(text):
        if match.start() <= offset < match.end():
            token = match
            break
        if match.end() == offset:  # Caret directly after a name or literal.
            token = match
        if match.start() > offset:
            break
    if token is None or token.lastgroup in ('comment', 'char'):
        return None
    if selected and selection_end > token.end():
        # A fully selected dotted type is useful, arbitrary expressions are not.
        if re.fullmatch(IDENT + r'(?:\.' + IDENT + r')+', selected):
            descriptor = 'L' + selected.replace('.', '/') + ';'
            if descriptor in classes:
                return ReferenceTarget(descriptor, 'type refs')
        return None
    if token.lastgroup == 'string':
        value = selected if selected and not selected.startswith('"') else token.group()[1:-1]
        escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f', '"': '"', "'": "'", '\\': '\\'}
        value = re.sub(r'\\(u[0-9a-fA-F]{4}|[0-7]{1,3}|.)',
                       lambda m: chr(int(m[1][1:], 16)) if m[1].startswith('u') and len(m[1]) == 5
                       else chr(int(m[1], 8)) if m[1][0] in '01234567'
                       else escapes.get(m[1], '\\' + m[1]), value)
        return ReferenceTarget(value, 'string refs') if value else None
    name = token.group()
    if name in KEYWORDS:
        return None
    before = text[:token.start()]
    after = text[token.end():]
    # Include the qualifier only for type queries; names remain broad for members.
    qualifier = re.search(r'(?:' + IDENT + r'\.)+$', before)
    full_name = (qualifier.group() if qualifier else '') + name
    descriptor = 'L' + full_name.replace('.', '/') + ';'
    type_prefix = before[:qualifier.start()] if qualifier else before
    declared_type = re.search(r'\b(?:class|interface|enum|extends|implements|new|instanceof|import)\s+$', type_prefix)
    known_type = descriptor in classes or any(c.rsplit('/', 1)[-1].rstrip(';') == name for c in classes)
    member_access = before.rstrip().endswith('.')
    if re.match(r'\s*\(', after) and not declared_type:
        return ReferenceTarget(name, 'method refs')
    line_prefix = before.rsplit('\n', 1)[-1]
    field_declaration = re.match(r'\s*(?:public|private|protected)\s+(?:(?:static|final|transient|volatile)\s+)*[\w.$<>\[\], ?]+\s+$', line_prefix)
    if field_declaration and not declared_type:
        return ReferenceTarget(name, 'field refs')
    type_usage = re.match(r'\s*(?:'+IDENT+r'\b|\[|\.|<)', after)
    if declared_type or (known_type and type_usage and (not member_access or descriptor in classes)):
        return ReferenceTarget(descriptor if descriptor in classes or '.' in full_name else name, 'type refs')
    if re.match(r'\s*\(', after):
        return ReferenceTarget(name, 'method refs')
    # Unqualified declarations of a field; local variable declarations remain ambiguous.
    if member_access or field_declaration:
        return ReferenceTarget(name, 'field refs')
    return ReferenceTarget(name, None)


def scoped_reference(text, offset, current, lookup, target):
    """Build the same receiver query as definition navigation, including fields."""
    from dataclasses import asdict
    from .navigation import navigation_query, identifier_at
    from .variables import find_variable_definition
    # infer_reference accepts a caret immediately following the token.
    if identifier_at(text, offset) is None and offset:
        offset -= 1
    if find_variable_definition(text, offset, current) is not None:
        return None
    kind = 'method' if target.kind == 'method refs' else 'field'
    query = navigation_query(text, offset, current, lookup, member_kind=kind)
    return asdict(query) if query else None
